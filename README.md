# Warranty certificates (Záruční listy)

A small **Flask** web application for storing and managing **warranty certificates** (receipts, PDFs, images): products, sellers, purchase and warranty dates, categories, notes, thumbnails, and attached documents. The UI is responsive (Tailwind CSS) and backed by **SQLite**.

---

## Features

### Dashboard

- Summary counts: **total** certificates, **active** (warranty end date on or after today), **ending soon** (warranty ends in fewer than 30 days), and **expired**.
- Quick link to the list filtered for “ending soon”.

### Warranty certificates

- **List** with optional filters: product name, seller, category, status (all / active / ending soon / expired).
- **Add** and **edit** entries with:
  - Product name (required), seller, purchase date, warranty end date (required), category, free-text note.
  - **Thumbnail** (image; resized server-side).
  - **Attachment** (PDF or image): each upload is **normalized to an A4 PDF** — pages are **JPEG** streams (PyMuPDF embeds DCT directly; long edge about **1500 px**, quality tuned for photos/scans), scaled to fit **90%** of the page while **preserving aspect ratio** (centered). Input PDFs may have up to **40** pages (one output page per input page).
- **Delete** with confirmation; associated uploaded files are removed when replaced or deleted.

### Categories

- Create, rename, and delete categories used on warranty forms.
- Deletion is blocked if any warranty still references the category.

### Access control

- **Single shared password** (no per-user accounts), configured via environment variable.
- Optional **“stay signed in”**: when enabled, a long-lived cookie keeps the session; session lifetime matches the long-lived cookie policy (see below).

### Internationalization (i18n)

- **Multiple languages** via JSON files in the `locales/` directory (one file per locale, e.g. `cs.json`, `en.json`).
- **Custom locales**: add a new `*.json` file whose basename becomes the locale code (lowercase letters, digits, `_`, `-`; see `i18n.py` for the exact pattern). Use `_meta.label` in the JSON for the human-readable name shown in the language selector.
- **Merge / fallback**: Czech (`cs`) is the base; English (`en`) overrides where defined; any other locale overrides on top of that—so partial translation files are enough.
- **Flat or nested keys** in JSON are supported; keys starting with `_` (except `_meta`) are ignored when flattening nested structures.
- **Locale detection order**:
  1. Cookie `zl_lang` (user choice),
  2. else `Accept-Language` from the browser,
  3. else default `cs`.
- **Language switcher** in the main layout and on the login page; switching sets `zl_lang` and redirects back safely (same host only).
- **Hot reload of translations**: if `locales/` or any `*.json` inside it changes, the next request rescans the directory (mtime-based cache invalidation).

### Long-lived cookies (effectively permanent)

- **UI language** cookie (`zl_lang`) and **“stay signed in”** use a long `max_age` from `i18n.COOKIE_MAX_AGE` (intended as many decades; capped to **~68 years** so `Max-Age` fits a signed 32-bit second count reliably in browsers and stacks).
- Flask **`PERMANENT_SESSION_LIFETIME`** matches the same duration so permanent sessions align with that policy.

---

## Tech stack

| Layer        | Technology                          |
| ------------ | ----------------------------------- |
| Runtime      | Python 3.12+ (3.x generally fine)   |
| Web          | Flask 3.x                           |
| Database     | SQLite (`sqlite3` in the stdlib)   |
| Images / PDF | Pillow, PyMuPDF (rasterize PDF), ReportLab (build A4 PDF) |
| Styling      | Tailwind CSS 3 (built to static CSS) |

---

## Configuration

Environment variables (optional `.env` in the project root):

| Variable        | Description |
| --------------- | ----------- |
| `APP_PASSWORD`  | Password used to sign in (default in code is only for development). |
| `SECRET_KEY`    | Flask secret key for sessions (set a strong value in production). |
| `SQLITE_PATH`   | Path to the SQLite database file (default: `data.db` next to `app.py`). |
| `PORT`          | HTTP port when running `python app.py` (default `8087`). |
| `FLASK_DEBUG`   | `1` / `true` / `yes` to enable Flask debug server. |

Thumbnails and warranty PDFs are stored as **BLOBs in SQLite** (no `uploads/` directory). Maximum upload size is **16 MB** per request.

---

## Running locally

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:8087` (or the port from `PORT`).

### CSS (Tailwind)

If you change templates or Tailwind sources:

```bash
npm ci
npm run build:css
```

---

## Docker

The `Dockerfile` builds Tailwind in a Node stage, then a slim Python image with `app.py`, `i18n.py`, `warranty_pdf.py`, `locales/`, `templates/`, and compiled CSS. For **Synology** (or similar), see `docker-compose.synology.yml`: mount a persistent volume for the **SQLite database directory** (e.g. `/data` → `SQLITE_PATH=/data/data.db`).

---

## Project layout (high level)

| Path            | Role |
| --------------- | ---- |
| `app.py`        | Routes, DB schema, SQLite BLOBs for files, auth. |
| `warranty_pdf.py` | Resize uploads, rasterize PDF pages, build A4 PDF output. |
| `i18n.py`       | Locale scanning, resolution, translation helpers, cookie max-age constant. |
| `locales/*.json`| Translation strings and optional `_meta`. |
| `templates/`    | Jinja2 HTML. |
| `static/`       | Built CSS and assets. |

---

## Security notes

- This app is designed for **trusted / private** networks (e.g. home NAS). It uses a **single password**, not multi-user authentication.
- **“Stay signed in”** stores credentials in a cookie in a way that extends the session; treat the host and HTTPS termination accordingly in production.

---

## License / name

**Záruční listy** is Czech for “warranty sheets” or “warranty certificates.” Repository name and branding may stay in Czech while the app UI is available in multiple languages via `locales/`.
