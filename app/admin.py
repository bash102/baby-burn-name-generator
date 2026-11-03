"""PIN-protected owner pages: dashboard, content review/add, reprint. HTTP Basic, password = ADMIN_PIN."""
import hmac
import logging
import re
from io import BytesIO
from urllib.parse import quote, urlparse

from flask import Blueprint, Response, abort, current_app, jsonify, redirect, render_template, request

from app.badge import render_badge
from app.config import PROVIDERS
from app.content import KINDS, STATUSES, ContentError, append_entry, set_status
from app.printer import PrintError
from app.transform import get_provider

log = logging.getLogger(__name__)
bp = Blueprint("admin", __name__, url_prefix="/admin")
SAMPLE_TAGLINE = "Dusty since the womb"
SAMPLE_BANNER = "Using public sample content — set CONTENT_DIR to the private content repo to edit"
OPTION_SLOTS = "abcd"


def _st():
    return current_app.extensions["babyburn"]


@bp.before_request
def _guard():
    pin = _st().cfg.admin_pin
    if pin in ("", "change-me"):
        return Response("Admin is disabled: set ADMIN_PIN in .env to a real PIN.", 503)
    auth = request.authorization  # any username, constant-time compare on the password
    if not auth or not hmac.compare_digest((auth.password or "").encode(), pin.encode()):
        return Response("PIN required", 401, {"WWW-Authenticate": 'Basic realm="Baby Burn admin"'})
    if request.method == "POST":  # CSRF: browsers always send Origin (or Referer) on cross-site POSTs
        src = request.headers.get("Origin") or request.headers.get("Referer") or ""
        if urlparse(src).netloc != request.host:
            return jsonify(error="cross-origin request refused"), 403


def _write(fn):
    """Run a content write, reload the kiosk, return an error string or None."""
    st = _st()
    if st.cfg.content_is_sample:
        return SAMPLE_BANNER
    try:
        fn()
    except ContentError as e:
        st.reload_content()  # surfaces broken files as the dashboard's content_error banner
        return str(e)
    st.reload_content()
    return st.content_error  # on a failed reload the kiosk keeps the old content


def _banner(st):
    return SAMPLE_BANNER if st.cfg.content_is_sample else None


@bp.get("")
def dashboard():
    st = _st()
    by_kind = {k: {s: sum(i.status == s for i in getattr(st.content, k)) for s in sorted(STATUSES)} for k in KINDS}
    return render_template(
        "admin.html", page="dashboard", st=st, by_kind=by_kind, remaining=st.names.remaining(),
        by_tag=sorted(st.names.remaining_by_tag().items()), low=st.cfg.low_names_warning,
        providers=sorted(PROVIDERS), recent=list(enumerate(st.recent_badges))[::-1],
        album_url=st.cloud.album_url(), album_count=len(st.cloud.album.entries()),
        sample_banner=_banner(st), msg=request.args.get("msg"))


@bp.get("/review")
def review():
    st, a = _st(), request.args
    kind = a.get("kind") if a.get("kind") in KINDS else "names"
    status, tag = a.get("status", "draft"), a.get("tag", "")
    rows = [r for r in getattr(st.content, kind)
            if (not status or r.status == status) and (not tag or tag in getattr(r, "tags", ()))
            and (not a.get("needs_owner") or getattr(r, "needs_owner", False))]
    if kind == "names":  # favorites first, then owner-attention rows (sort is stable)
        rows.sort(key=lambda n: (n.note != "favorite", not n.needs_owner))
    return render_template(
        "admin.html", page="review", st=st, kind=kind, status=status, tag=tag, rows=rows,
        all_tags=sorted({t for n in st.content.names for t in n.tags}), statuses=sorted(STATUSES),
        needs_owner=bool(a.get("needs_owner")), msg=a.get("msg"), sample_banner=_banner(st))


@bp.post("/status")
def status():
    d = request.get_json(silent=True) or request.form
    st = _st()
    err = _write(lambda: set_status(st.cfg.content_dir, d.get("kind"), d.get("id"), d.get("status")))
    return (jsonify(error=err), 400) if err else jsonify(ok=True)


def _slug(text: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "x"
    id_, n = base, 2
    while id_ in taken:
        id_, n = f"{base}-{n}", n + 1
    return id_


def _tags(raw: str) -> list[str]:
    return [t.strip().lower() for t in raw.split(",") if t.strip()]


def _entry(kind: str, f, taken: set[str]) -> dict:
    if kind == "names":
        name = f.get("name", "").strip()
        return {"id": _slug(name, taken), "name": name, "tags": _tags(f.get("tags", "")),
                "status": "approved", "needs_owner": False, "note": ""}
    if kind == "taglines":
        text = f.get("text", "").strip()
        return {"id": _slug(text, taken), "text": text, "status": "approved"}
    prompt = f.get("prompt", "").strip()
    options = []
    for s in OPTION_SLOTS:
        if text := f.get(f"opt_{s}", "").strip():
            o = {"id": s, "text": text}
            if f.get(f"correct_{s}"):
                o["correct"] = True
            if tags := _tags(f.get(f"tags_{s}", "")):
                o["tags"] = tags
            options.append(o)
    q = {"id": _slug(prompt, taken), "type": "multi" if f.get("type") == "multi" else "single",
         "prompt": prompt, "options": options, "status": "approved", "needs_owner": False}
    if quip := f.get("quip", "").strip():
        q["quip"] = quip
    return q


@bp.post("/add")
def add():
    st, kind = _st(), request.form.get("kind")
    if kind not in KINDS:
        abort(400)
    entry = _entry(kind, request.form, {i.id for i in getattr(st.content, kind)})
    err = _write(lambda: append_entry(st.cfg.content_dir, kind, entry))
    return redirect(f"/admin/review?kind={kind}&status=approved&msg={quote(err or 'Added.')}")


@bp.get("/preview/<name_id>.png")
def preview(name_id):
    n = next((n for n in _st().content.names if n.id == name_id), None)
    if n is None:
        abort(404)
    buf = BytesIO()
    render_badge(n.name, SAMPLE_TAGLINE, 3).save(buf, "PNG")
    return Response(buf.getvalue(), mimetype="image/png", headers={"Cache-Control": "private, max-age=300"})


def _print(img):
    try:
        _st().printer(img)
    except PrintError as e:
        log.exception("admin print failed")
        return jsonify(error=str(e)), 502
    return jsonify(ok=True)


@bp.post("/reprint")
def reprint():
    d = request.get_json(silent=True) or request.form
    try:
        _, display, tagline, first_burn = list(_st().recent_badges)[int(d["index"])]
    except (KeyError, ValueError, IndexError, TypeError):
        return jsonify(error="no such badge"), 400
    return _print(render_badge(display, tagline, first_burn))


@bp.post("/test-print")
def test_print():
    return _print(render_badge("Test Print Baby", "Printer check", 3))


@bp.post("/provider")
def provider():
    st, name = _st(), (request.get_json(silent=True) or request.form).get("name")
    try:
        p = get_provider(name, st.cfg)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    st.provider, st.provider_name = p, name  # runtime only; .env stays the default
    return jsonify(ok=True)
