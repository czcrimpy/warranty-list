import json
import re
from pathlib import Path

from flask import g, has_request_context, request

LOCALES_DIR = Path(__file__).resolve().parent / "locales"
DEFAULT_LOCALE = "cs"
FALLBACK_LOCALE = "en"
LANG_COOKIE = "zl_lang"
# Target ~99 years, but cap to signed 32-bit seconds so Set-Cookie Max-Age and
# some session stacks do not misbehave (e.g. overflow / ignored values).
_COOKIE_99Y = 99 * 365 * 24 * 60 * 60
_COOKIE_MAX_INT32 = 2**31 - 1
COOKIE_MAX_AGE = min(_COOKIE_99Y, _COOKIE_MAX_INT32)

_locales_flat = {}
_locales_meta = {}
_locales_dir_mtime = 0.0


def _flatten(d, prefix=""):
    out = {}
    if not isinstance(d, dict):
        return out
    for k, v in d.items():
        if str(k).startswith("_"):
            continue
        key = f"{prefix}.{k}" if prefix else str(k)
        if isinstance(v, dict):
            out.update(_flatten(v, key))
        else:
            out[key] = str(v)
    return out


def _scan_locales():
    global _locales_flat, _locales_meta, _locales_dir_mtime
    if not LOCALES_DIR.is_dir():
        LOCALES_DIR.mkdir(parents=True, exist_ok=True)
        _locales_flat = {}
        _locales_meta = {}
        _locales_dir_mtime = 0.0
        return
    mt = 0.0
    for p in LOCALES_DIR.glob("*.json"):
        if p.name.startswith("_") or p.name.startswith("."):
            continue
        try:
            mt = max(mt, p.stat().st_mtime)
        except OSError:
            continue
    if mt == _locales_dir_mtime and _locales_flat:
        return
    _locales_dir_mtime = mt
    flats = {}
    metas = {}
    for p in sorted(LOCALES_DIR.glob("*.json")):
        if p.name.startswith("_") or p.name.startswith("."):
            continue
        code = p.stem.lower()
        if not code or not re.match(r"^[a-z][a-z0-9_-]{0,31}$", code):
            continue
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            continue
        meta = {}
        if isinstance(raw, dict) and "_meta" in raw and isinstance(raw.get("_meta"), dict):
            meta = dict(raw["_meta"])
            raw = {k: v for k, v in raw.items() if k != "_meta"}
        def _is_flat_map(d):
            if not isinstance(d, dict) or not d:
                return False
            return not any(isinstance(v, dict) for v in d.values())

        if isinstance(raw, dict) and _is_flat_map(raw):
            flats[code] = {k: str(v) for k, v in raw.items()}
        else:
            flats[code] = _flatten(raw) if isinstance(raw, dict) else {}
        metas[code] = meta
    _locales_flat = flats
    _locales_meta = metas


def available_locales():
    _scan_locales()
    return sorted(_locales_flat.keys())


def locale_label(code):
    _scan_locales()
    m = _locales_meta.get(code) or {}
    if m.get("label"):
        return m["label"]
    return code.upper()


def resolve_locale():
    _scan_locales()
    avail = available_locales()
    if not avail:
        return DEFAULT_LOCALE
    ck = (request.cookies.get(LANG_COOKIE) or "").strip().lower()
    if ck in _locales_flat:
        return ck
    try:
        best = request.accept_languages.best_match(avail)
    except (AttributeError, TypeError, ValueError):
        best = None
    if best:
        return best
    if DEFAULT_LOCALE in _locales_flat:
        return DEFAULT_LOCALE
    return avail[0]


def _flat_for_lang(code):
    _scan_locales()
    cs = _locales_flat.get("cs", {})
    en = _locales_flat.get("en", {})
    if code == "cs":
        return dict(cs)
    if code == "en":
        return {**cs, **en}
    ow = _locales_flat.get(code, {})
    return {**cs, **en, **ow}


def translate(key, **kwargs):
    if has_request_context():
        lang = getattr(g, "lang", DEFAULT_LOCALE)
    else:
        lang = DEFAULT_LOCALE
    flat = _flat_for_lang(lang)
    s = flat.get(key)
    if s is None:
        s = key
    try:
        return s.format(**kwargs) if kwargs else s
    except (KeyError, ValueError):
        return s


def _(key, **kwargs):
    return translate(key, **kwargs)


def register_i18n(app):
    @app.context_processor
    def _inject():
        return dict(
            t=translate,
            _=_,
            lang=getattr(g, "lang", DEFAULT_LOCALE) if has_request_context() else DEFAULT_LOCALE,
            available_locales=available_locales,
            locale_label=locale_label,
            LANG_COOKIE=LANG_COOKIE,
        )
