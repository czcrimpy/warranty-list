import os
import secrets
import sqlite3
import uuid
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from flask import Flask, flash, g, redirect, render_template, request, send_from_directory, session, url_for
from PIL import Image, ImageOps
from werkzeug.utils import secure_filename

from i18n import (
    COOKIE_MAX_AGE,
    LANG_COOKIE,
    LOCALES_DIR,
    available_locales,
    register_i18n,
    resolve_locale,
    translate,
    _scan_locales,
)

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
DB_PATH = Path(os.environ.get("SQLITE_PATH", str(BASE_DIR / "data.db")))

load_dotenv(BASE_DIR / ".env")

ALLOWED_THUMB = {"png", "jpg", "jpeg", "gif", "webp"}
ALLOWED_DOC = {"pdf", "png", "jpg", "jpeg", "gif", "webp"}
THUMB_MAX_EDGE = 320

REMEMBER_COOKIE = "zl_remember"
PUBLIC_ENDPOINTS = frozenset({"login", "static", "set_language"})

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev")
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(seconds=COOKIE_MAX_AGE)

register_i18n(app)


def app_password():
    return os.environ.get("APP_PASSWORD", "admin1233")


def password_ok(pw):
    a = (pw or "").encode("utf-8")
    b = app_password().encode("utf-8")
    if len(a) != len(b):
        return False
    return secrets.compare_digest(a, b)


def safe_next(url):
    if not url or not isinstance(url, str):
        return ""
    u = url.strip()
    if not u.startswith("/") or u.startswith("//"):
        return ""
    return u


def safe_lang_redirect():
    ref = request.referrer
    if not ref:
        return redirect(url_for("dashboard"))
    try:
        r = urlparse(ref)
        b = urlparse(request.url_root)
    except ValueError:
        return redirect(url_for("dashboard"))
    if r.scheme in ("http", "https") and r.netloc == b.netloc:
        path = r.path or "/"
        if not path.startswith("/"):
            path = "/" + path
        qs = ("?" + r.query) if r.query else ""
        return redirect(path + qs)
    return redirect(url_for("dashboard"))


def cz_date(value):
    if value is None or value == "":
        return "—"
    if isinstance(value, (date, datetime)):
        return value.strftime("%d.%m.%Y")
    s = str(value).strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        y, m, d = s[:10].split("-")
        if len(y) == 4 and y.isdigit() and m.isdigit() and d.isdigit():
            return f"{int(d):02d}.{int(m):02d}.{y}"
    return s


app.jinja_env.filters["cz_date"] = cz_date


def ensure_dirs():
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    ensure_dirs()
    with closing(get_conn()) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE
            );
            CREATE TABLE IF NOT EXISTS warranties (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_name TEXT NOT NULL,
                seller TEXT,
                purchase_date TEXT,
                warranty_until TEXT NOT NULL,
                category_id INTEGER REFERENCES categories(id),
                note TEXT,
                thumbnail_image TEXT,
                warranty_file TEXT
            );
            """
        )
        conn.commit()


def allowed_ext(filename, allowed):
    if not filename or "." not in filename:
        return False
    return filename.rsplit(".", 1)[1].lower() in allowed


def save_upload(upload, prefix, allowed):
    if not upload or upload.filename == "":
        return None
    raw = secure_filename(upload.filename)
    if not raw or not allowed_ext(raw, allowed):
        return None
    ext = raw.rsplit(".", 1)[1].lower()
    fname = f"{prefix}_{uuid.uuid4().hex}.{ext}"
    fpath = UPLOAD_DIR / fname
    upload.save(fpath)
    return f"uploads/{fname}"


def save_thumbnail(upload):
    if not upload or upload.filename == "":
        return None
    raw = secure_filename(upload.filename)
    if not raw or not allowed_ext(raw, ALLOWED_THUMB):
        return None
    fname = f"thumb_{uuid.uuid4().hex}.jpg"
    fpath = UPLOAD_DIR / fname
    resample = getattr(Image, "Resampling", Image).LANCZOS
    try:
        upload.stream.seek(0)
        img = Image.open(upload.stream)
        img.load()
        img = ImageOps.exif_transpose(img)
        if img.mode == "RGB":
            rgb = img
        else:
            rgba = img.convert("RGBA")
            rgb = Image.new("RGB", rgba.size, (255, 255, 255))
            rgb.paste(rgba, mask=rgba.split()[3])
        rgb.thumbnail((THUMB_MAX_EDGE, THUMB_MAX_EDGE), resample)
        rgb.save(fpath, "JPEG", quality=88, optimize=True)
        return f"uploads/{fname}"
    except Exception:
        upload.stream.seek(0)
        return save_upload(upload, "thumb", ALLOWED_THUMB)


def parse_db_date(s):
    if not s:
        return None
    y, m, d = (int(x) for x in s.split("-"))
    return date(y, m, d)


def is_expired(warranty_until_str, today):
    w = parse_db_date(warranty_until_str)
    if not w:
        return False
    return w < today


def days_until_warranty_end(warranty_until_str, today):
    w = parse_db_date(warranty_until_str)
    if not w:
        return None
    return (w - today).days


def is_ending_soon(warranty_until_str, today):
    if is_expired(warranty_until_str, today):
        return False
    d = days_until_warranty_end(warranty_until_str, today)
    return d is not None and 0 <= d < 30


def like_pattern(term):
    term = (term or "").strip()
    if not term:
        return None
    return (
        "%"
        + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        + "%"
    )


def delete_file_if_exists(rel_path):
    if not rel_path:
        return
    norm = str(rel_path).replace("\\", "/")
    if not norm.startswith("uploads/"):
        return
    p = (BASE_DIR / rel_path).resolve()
    root = UPLOAD_DIR.resolve()
    if root != p and root not in p.parents:
        return
    try:
        if p.is_file():
            p.unlink()
    except OSError:
        pass


@app.route("/uploads/<path:filename>")
def serve_upload(filename):
    return send_from_directory(UPLOAD_DIR, filename)


@app.route("/set-language/<code>", methods=["GET"])
def set_language(code):
    _scan_locales()
    c = (code or "").strip().lower()
    if c not in available_locales():
        flash(translate("flash.lang_unknown"), "error")
        return safe_lang_redirect()
    resp = safe_lang_redirect()
    resp.set_cookie(
        LANG_COOKIE,
        c,
        max_age=COOKIE_MAX_AGE,
        path="/",
        samesite="Lax",
    )
    return resp


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("logged_in"):
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        pw = request.form.get("password", "")
        remember = request.form.get("remember")
        nxt = safe_next(request.form.get("next") or request.args.get("next") or "")
        if password_ok(pw):
            session["logged_in"] = True
            session.permanent = bool(remember)
            dest = nxt or url_for("dashboard")
            resp = redirect(dest)
            if remember:
                resp.set_cookie(
                    REMEMBER_COOKIE,
                    pw.strip(),
                    max_age=COOKIE_MAX_AGE,
                    httponly=True,
                    samesite="Lax",
                    path="/",
                )
            else:
                resp.delete_cookie(REMEMBER_COOKIE, path="/")
            return resp
        flash(translate("flash.invalid_password"), "error")
    return render_template(
        "login.html",
        next=safe_next(
            request.form.get("next") or request.args.get("next") or ""
        ),
    )


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    resp = redirect(url_for("login"))
    resp.delete_cookie(REMEMBER_COOKIE, path="/")
    return resp


@app.route("/")
def dashboard():
    today = date.today().isoformat()
    with closing(get_conn()) as conn:
        total = conn.execute("SELECT COUNT(*) AS c FROM warranties").fetchone()["c"]
        active = conn.execute(
            "SELECT COUNT(*) AS c FROM warranties WHERE warranty_until >= ?",
            (today,),
        ).fetchone()["c"]
        expired = conn.execute(
            "SELECT COUNT(*) AS c FROM warranties WHERE warranty_until < ?",
            (today,),
        ).fetchone()["c"]
        soon_limit = (date.fromisoformat(today) + timedelta(days=30)).isoformat()
        ending_soon = conn.execute(
            """
            SELECT COUNT(*) AS c FROM warranties
            WHERE warranty_until >= ? AND warranty_until < ?
            """,
            (today, soon_limit),
        ).fetchone()["c"]
    return render_template(
        "dashboard.html",
        total=total,
        active=active,
        expired=expired,
        ending_soon=ending_soon,
        today=date.today(),
    )


@app.route("/warranties")
def warranties_list():
    today = date.today()
    today_iso = today.isoformat()
    product_q = (request.args.get("product") or "").strip()
    seller_q = (request.args.get("seller") or "").strip()
    category_raw = (request.args.get("category_id") or "").strip()
    status = (request.args.get("status") or "").strip()

    conditions = []
    params = []

    pp = like_pattern(product_q)
    if pp is not None:
        conditions.append("COALESCE(w.product_name, '') LIKE ? ESCAPE '\\'")
        params.append(pp)

    sp = like_pattern(seller_q)
    if sp is not None:
        conditions.append("COALESCE(w.seller, '') LIKE ? ESCAPE '\\'")
        params.append(sp)

    if category_raw.isdigit():
        conditions.append("w.category_id = ?")
        params.append(int(category_raw))

    if status == "active":
        conditions.append("w.warranty_until >= ?")
        params.append(today_iso)
    elif status == "expired":
        conditions.append("w.warranty_until < ?")
        params.append(today_iso)
    elif status == "soon":
        soon_limit = (today + timedelta(days=30)).isoformat()
        conditions.append("w.warranty_until >= ?")
        params.append(today_iso)
        conditions.append("w.warranty_until < ?")
        params.append(soon_limit)

    where_sql = " AND ".join(conditions) if conditions else "1=1"
    sql = f"""
            SELECT w.*, c.name AS category_name
            FROM warranties w
            LEFT JOIN categories c ON c.id = w.category_id
            WHERE {where_sql}
            ORDER BY w.warranty_until DESC, w.id DESC
            """

    has_filters = bool(pp) or bool(sp) or bool(category_raw.isdigit()) or status in (
        "active",
        "expired",
        "soon",
    )
    filters = {
        "product": product_q,
        "seller": seller_q,
        "category_id": category_raw if category_raw.isdigit() else "",
        "status": status if status in ("active", "expired", "soon") else "",
    }

    with closing(get_conn()) as conn:
        rows = conn.execute(sql, params).fetchall()
        categories = conn.execute(
            "SELECT id, name FROM categories ORDER BY name"
        ).fetchall()

    items = []
    for r in rows:
        d = dict(r)
        d["expired"] = is_expired(d["warranty_until"], today)
        d["ending_soon"] = is_ending_soon(d["warranty_until"], today)
        d["days_left"] = days_until_warranty_end(d["warranty_until"], today)
        items.append(d)
    return render_template(
        "warranties.html",
        warranties=items,
        today=today,
        categories=categories,
        filters=filters,
        has_filters=has_filters,
    )


def load_categories():
    with closing(get_conn()) as conn:
        return conn.execute("SELECT id, name FROM categories ORDER BY name").fetchall()


@app.route("/add", methods=["GET", "POST"])
def add_warranty():
    if request.method == "POST":
        product_name = (request.form.get("product_name") or "").strip()
        seller = (request.form.get("seller") or "").strip()
        purchase_date = (request.form.get("purchase_date") or "").strip() or None
        warranty_until = (request.form.get("warranty_until") or "").strip()
        category_id = request.form.get("category_id") or None
        note = (request.form.get("note") or "").strip() or None
        if not product_name or not warranty_until:
            flash(translate("flash.warranty_required"), "error")
            return render_template("warranty_form.html", categories=load_categories(), form=request.form)
        cat_val = int(category_id) if category_id else None
        thumb = save_thumbnail(request.files.get("thumbnail"))
        doc = save_upload(request.files.get("warranty_file"), "doc", ALLOWED_DOC)
        with closing(get_conn()) as conn:
            conn.execute(
                """
                INSERT INTO warranties
                (product_name, seller, purchase_date, warranty_until, category_id, note, thumbnail_image, warranty_file)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (product_name, seller, purchase_date, warranty_until, cat_val, note, thumb, doc),
            )
            conn.commit()
        flash(translate("flash.warranty_saved"), "ok")
        return redirect(url_for("warranties_list"))
    return render_template("warranty_form.html", categories=load_categories(), form=None)


@app.route("/edit/<int:wid>", methods=["GET", "POST"])
def edit_warranty(wid):
    with closing(get_conn()) as conn:
        row = conn.execute("SELECT * FROM warranties WHERE id = ?", (wid,)).fetchone()
    if not row:
        flash(translate("flash.warranty_not_found"), "error")
        return redirect(url_for("warranties_list"))
    if request.method == "POST":
        product_name = (request.form.get("product_name") or "").strip()
        seller = (request.form.get("seller") or "").strip()
        purchase_date = (request.form.get("purchase_date") or "").strip() or None
        warranty_until = (request.form.get("warranty_until") or "").strip()
        category_id = request.form.get("category_id") or None
        note = (request.form.get("note") or "").strip() or None
        if not product_name or not warranty_until:
            flash(translate("flash.warranty_required"), "error")
            return render_template(
                "warranty_form.html",
                categories=load_categories(),
                form=request.form,
                warranty=dict(row),
                edit=True,
            )
        cat_val = int(category_id) if category_id else None
        thumb = save_thumbnail(request.files.get("thumbnail"))
        doc = save_upload(request.files.get("warranty_file"), "doc", ALLOWED_DOC)
        thumb_path = thumb or row["thumbnail_image"]
        doc_path = doc or row["warranty_file"]
        if thumb and row["thumbnail_image"]:
            delete_file_if_exists(row["thumbnail_image"])
        if doc and row["warranty_file"]:
            delete_file_if_exists(row["warranty_file"])
        with closing(get_conn()) as conn:
            conn.execute(
                """
                UPDATE warranties SET
                    product_name = ?, seller = ?, purchase_date = ?, warranty_until = ?,
                    category_id = ?, note = ?, thumbnail_image = ?, warranty_file = ?
                WHERE id = ?
                """,
                (
                    product_name,
                    seller,
                    purchase_date,
                    warranty_until,
                    cat_val,
                    note,
                    thumb_path,
                    doc_path,
                    wid,
                ),
            )
            conn.commit()
        flash(translate("flash.warranty_updated"), "ok")
        return redirect(url_for("warranties_list"))
    return render_template(
        "warranty_form.html",
        categories=load_categories(),
        form=None,
        warranty=dict(row),
        edit=True,
    )


@app.route("/delete/<int:wid>", methods=["POST"])
def delete_warranty(wid):
    with closing(get_conn()) as conn:
        row = conn.execute(
            "SELECT thumbnail_image, warranty_file FROM warranties WHERE id = ?",
            (wid,),
        ).fetchone()
        if row:
            conn.execute("DELETE FROM warranties WHERE id = ?", (wid,))
            conn.commit()
            delete_file_if_exists(row["thumbnail_image"])
            delete_file_if_exists(row["warranty_file"])
            flash(translate("flash.warranty_deleted"), "ok")
        else:
            flash(translate("flash.warranty_not_found"), "error")
    return redirect(url_for("warranties_list"))


@app.route("/categories", methods=["GET", "POST"])
def categories():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        if not name:
            flash(translate("flash.category_name_required"), "error")
        else:
            try:
                with closing(get_conn()) as conn:
                    conn.execute("INSERT INTO categories (name) VALUES (?)", (name,))
                    conn.commit()
                flash(translate("flash.category_created"), "ok")
            except sqlite3.IntegrityError:
                flash(translate("flash.category_exists"), "error")
        return redirect(url_for("categories"))
    with closing(get_conn()) as conn:
        rows = conn.execute("SELECT id, name FROM categories ORDER BY name").fetchall()
    return render_template("categories.html", categories=rows)


@app.route("/categories/edit/<int:cid>", methods=["GET", "POST"])
def edit_category(cid):
    with closing(get_conn()) as conn:
        row = conn.execute("SELECT * FROM categories WHERE id = ?", (cid,)).fetchone()
    if not row:
        flash(translate("flash.category_not_found"), "error")
        return redirect(url_for("categories"))
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        if not name:
            flash(translate("flash.category_name_required"), "error")
            return render_template("category_form.html", category=dict(row))
        try:
            with closing(get_conn()) as conn:
                conn.execute("UPDATE categories SET name = ? WHERE id = ?", (name, cid))
                conn.commit()
            flash(translate("flash.category_updated"), "ok")
            return redirect(url_for("categories"))
        except sqlite3.IntegrityError:
            flash(translate("flash.category_exists"), "error")
    return render_template("category_form.html", category=dict(row))


@app.route("/categories/delete/<int:cid>", methods=["POST"])
def delete_category(cid):
    with closing(get_conn()) as conn:
        row = conn.execute("SELECT id FROM categories WHERE id = ?", (cid,)).fetchone()
        if not row:
            flash(translate("flash.category_not_found"), "error")
            return redirect(url_for("categories"))
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM warranties WHERE category_id = ?",
            (cid,),
        ).fetchone()["c"]
        if n > 0:
            flash(translate("flash.category_delete_blocked"), "error")
            return redirect(url_for("categories"))
        conn.execute("DELETE FROM categories WHERE id = ?", (cid,))
        conn.commit()
    flash(translate("flash.category_deleted"), "ok")
    return redirect(url_for("categories"))


_db_ok = False


@app.before_request
def _ensure():
    global _db_ok
    _scan_locales()
    g.lang = resolve_locale()

    ensure_dirs()
    if not _db_ok:
        init_db()
        _db_ok = True

    ep = request.endpoint
    if ep in PUBLIC_ENDPOINTS or ep is None:
        return
    if session.get("logged_in"):
        return
    ck = request.cookies.get(REMEMBER_COOKIE)
    if ck is not None and password_ok(ck):
        session["logged_in"] = True
        session.permanent = True
        return
    return redirect(url_for("login", next=request.full_path))


if __name__ == "__main__":
    import sys

    _scan_locales()
    if not available_locales():
        print(
            f"ERROR: No locale JSON files found in {LOCALES_DIR}. "
            "Copy the whole `locales/` folder next to app.py (and i18n.py).",
            file=sys.stderr,
        )
        sys.exit(1)

    port = int(os.environ.get("PORT", "8087"))
    debug = os.environ.get("FLASK_DEBUG", "").lower() in ("1", "true", "yes")
    app.run(host="0.0.0.0", port=port, debug=debug)
