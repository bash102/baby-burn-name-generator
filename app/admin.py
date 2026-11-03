"""PIN-protected owner pages: dashboard, content review/add, reprint. HTTP Basic, password = ADMIN_PIN."""
import hmac
import logging
import re
from io import BytesIO
from urllib.parse import quote, urlparse

from flask import Blueprint, Response, abort, current_app, jsonify, make_response, redirect, render_template, request

from app.badge import render_badge
from app.config import PROVIDERS
from app.content import KINDS, STATUSES, ContentError, append_entry, set_status
from app.transform import get_provider

log = logging.getLogger(__name__)
bp = Blueprint("admin", __name__, url_prefix="/admin")
SAMPLE_TAGLINE = "Dusty since the womb"
SAMPLE_BANNER = "Using public sample content. Set CONTENT_DIR to the private content repo to edit."
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


def _body():
    """The JSON object (or form) a POST sent; any other JSON is a 400."""
    if not request.is_json:
        return request.form
    d = request.get_json(silent=True)
    if not isinstance(d, dict):
        abort(make_response(jsonify(error="body must be a JSON object"), 400))
    return d


@bp.get("/")
def slash():
    return redirect("/admin")


@bp.get("")
def dashboard():
    st = _st()
    by_kind = {k: {s: sum(i.status == s for i in getattr(st.content, k)) for s in sorted(STATUSES)} for k in KINDS}
    return render_template(
        "admin.html", page="dashboard", st=st, by_kind=by_kind, remaining=st.names.remaining(),
        by_tag=sorted(st.names.remaining_by_tag().items()), low=st.cfg.low_names_warning,
        providers=sorted(PROVIDERS), recent=list(st.recent_badges)[::-1],
        album_url=st.cloud.album_url(), album_count=len(st.cloud.album.entries()),
        sample_banner=st.cfg.content_is_sample and SAMPLE_BANNER, msg=request.args.get("msg"))


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
        needs_owner=bool(a.get("needs_owner")), msg=a.get("msg"),
        sample_banner=st.cfg.content_is_sample and SAMPLE_BANNER)


@bp.post("/status")
def status():
    d = _body()
    st = _st()
    err = _write(lambda: set_status(st.cfg.content_dir, d.get("kind"), [d.get("id")], d.get("status")))
    return (jsonify(error=err), 400) if err else jsonify(ok=True)


@bp.post("/status-bulk")
def status_bulk():
    d, st = _body(), _st()
    ids = d.get("ids")
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        return jsonify(error="ids must be a list of strings"), 400
    if d.get("status") == "approved" and d.get("include_needs_owner") is not True:  # sent only with the filter on
        items = getattr(st.content, d["kind"]) if d.get("kind") in KINDS else ()
        if flagged := [i.id for i in items if i.id in ids and getattr(i, "needs_owner", False)]:
            return jsonify(error="needs your review, approve with that filter on: " + ", ".join(flagged)), 400
    err = _write(lambda: set_status(st.cfg.content_dir, d.get("kind"), ids, d.get("status")))
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
    except Exception as e:  # any printer fault (PrintError, USB OSError) is a JSON error, never a 500
        log.exception("admin print failed")
        return jsonify(error=str(e) or type(e).__name__), 502
    return jsonify(ok=True)


@bp.post("/reprint")
def reprint():
    id_ = _body().get("id")
    badge = next((b for b in list(_st().recent_badges) if b[0] == id_), None)
    if badge is None:
        return jsonify(error="no such badge"), 404
    _, _, display, tagline, first_burn, photo_url = badge
    return _print(render_badge(display, tagline, first_burn, qr_url=photo_url))


@bp.post("/test-print")
def test_print():
    return _print(render_badge("Test Print Baby", "Printer check", 3))


@bp.post("/provider")
def provider():
    st, name = _st(), _body().get("name")
    try:
        p = get_provider(name, st.cfg)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    st.provider, st.provider_name = p, name  # runtime only; .env stays the default
    return jsonify(ok=True)
