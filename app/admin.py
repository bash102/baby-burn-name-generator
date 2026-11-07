"""PIN-protected owner pages: dashboard, content review/add, reprint. HTTP Basic, password = ADMIN_PIN."""
import hashlib
import hmac
import logging
import os
import pwd
import random
import re
import shutil
import subprocess
import threading
import time
from io import BytesIO
from pathlib import Path
from urllib.parse import quote, urlparse

from flask import Blueprint, Response, abort, current_app, jsonify, make_response, redirect, render_template, request

from app import badge
from app.badge import render_badge
from app.coloring import page_files, pick_page
from app.config import PROVIDERS
from app.content import KINDS, STATUSES, ContentError, append_entry, set_status
from app.transform import get_provider

log = logging.getLogger(__name__)
bp = Blueprint("admin", __name__, url_prefix="/admin")
SAMPLE_TAGLINE = "Dusty since the womb"
SAMPLE_BANNER = "Using public sample content. Set CONTENT_DIR to the private content repo to edit."
OPTION_SLOTS = "abcd"
# Changes when the badge code or fonts change, so a deploy that alters the badge re-renders the previews.
LAYOUT = hashlib.sha256(b"".join(p.read_bytes() for p in (Path(badge.__file__), badge.NAME_FONT,
                                                           badge.TEXT_FONT))).hexdigest()[:12]
PREVIEW_IMMUTABLE = "private, max-age=2592000, immutable"
_write_lock = threading.Lock()


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
    """Run a content write, install its result in the kiosk, return an error string or None."""
    st = _st()
    if st.cfg.content_is_sample:
        return SAMPLE_BANNER
    with _write_lock:  # install in write order, so a slower earlier click never overwrites a newer one
        try:
            content = fn()
        except ContentError as e:
            st.reload_content()  # surfaces broken files as the dashboard's content_error banner
            return str(e)
        st.install_content(content)  # the writer already read and validated all of it: no second full load
    return None


def _body():
    """The JSON object (or form) a POST sent; any other JSON is a 400."""
    if not request.is_json:
        return request.form
    d = request.get_json(silent=True)
    if not isinstance(d, dict):
        abort(make_response(jsonify(error="body must be a JSON object"), 400))
    return d


GIT_DEPLOY_APP = "baby-burn-name-generator"
GIT_DEPLOY_TIMEOUT_S = 15
STATUS_TTL_S = 10  # /admin loads reuse one `git-deploy status` for this long
BUSY_MSG = "a deploy is running — retry in a minute"  # git-deploy holds its per-app lock while deploying
FLAG_LINE = re.compile(r"^\s*(PAUSED|BLOCKED)\b")  # git-deploy status: "  PAUSED (since …): …", "  BLOCKED: …"
UPDATE_ARGS = {  # action -> git-deploy args
    "pause": ["pause", GIT_DEPLOY_APP, "--reason", "paused from admin"],
    "resume": ["resume", GIT_DEPLOY_APP],
    "sync": ["sync", GIT_DEPLOY_APP, "--force"],
}
_status_cache = {"at": float("-inf"), "text": ""}
_status_lock = threading.Lock()


def _gd(argv):
    return subprocess.run(argv, capture_output=True, text=True, timeout=GIT_DEPLOY_TIMEOUT_S,
                          stdin=subprocess.DEVNULL)


def _detached(exe, args):
    """argv running git-deploy in its own transient systemd unit: the deploy restarts app-babyburn, which
    would kill a child in our cgroup mid-deploy. systemd-run returns once the unit starts (no child to reap)."""
    me, log_path = pwd.getpwuid(os.getuid()), _st().cfg.data_dir / "git-deploy-sync.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.touch()  # created by us, not by systemd as root
    return ["sudo", "-n", "systemd-run", f"--uid={me.pw_name}", "--collect", "--quiet",
            f"--unit=babyburn-update-{time.strftime('%Y%m%d-%H%M%S')}",
            f"--property=StandardOutput=append:{log_path}", f"--property=StandardError=append:{log_path}",
            f"--working-directory={me.pw_dir}", f"--setenv=HOME={me.pw_dir}",  # git-deploy finds ~/apps
            os.path.abspath(exe), *args]


def _status_text(exe):
    with _status_lock:  # concurrent loads share one call
        if time.monotonic() - _status_cache["at"] < STATUS_TTL_S:
            return _status_cache["text"]
        try:
            r = _gd([exe, "status", GIT_DEPLOY_APP])
            text = (r.stdout + r.stderr).strip() or f"git-deploy status exited {r.returncode}"
        except subprocess.TimeoutExpired:
            text = BUSY_MSG
        except (OSError, subprocess.SubprocessError) as e:
            text = f"git-deploy status failed: {e}"
        _status_cache.update(at=time.monotonic(), text=text)
        return text


def _updates():
    """Dashboard data for the Updates panel; None when git-deploy is not installed."""
    exe = shutil.which("git-deploy")
    if not exe:
        return None
    text = _status_text(exe)
    return {"status": text, "flags": [ln.strip() for ln in text.splitlines() if FLAG_LINE.match(ln)]}


@bp.post("/update/<action>")
def update(action):
    args = UPDATE_ARGS.get(action)
    if args is None:
        abort(404)
    exe = shutil.which("git-deploy")
    if not exe:
        return jsonify(error="git-deploy is not installed"), 503
    try:
        r = _gd(_detached(exe, args) if action == "sync" else [exe, *args])
    except subprocess.TimeoutExpired:
        return jsonify(error=BUSY_MSG), 504
    except (OSError, subprocess.SubprocessError) as e:
        log.exception("git-deploy %s failed", action)
        return jsonify(error=str(e) or type(e).__name__), 502
    finally:
        # Cleared after the action, so a page load during it can't re-cache the old state.
        _status_cache["at"] = float("-inf")
    if r.returncode:
        return jsonify(error=(r.stdout + r.stderr).strip() or f"exit {r.returncode}"), 502
    return jsonify(ok=True)


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
        updates=_updates(), coloring_pool=len(page_files(st.cfg.coloring_path)), sample_banner=st.cfg.content_is_sample and SAMPLE_BANNER, msg=request.args.get("msg"))


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
        sample_banner=st.cfg.content_is_sample and SAMPLE_BANNER, preview_key=preview_key)


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


def preview_key(name: str) -> str:
    """Cache key (and ?v=) of a name's preview: changes with the name text, tagline or badge layout."""
    return hashlib.sha256(repr((name, SAMPLE_TAGLINE, LAYOUT)).encode()).hexdigest()[:16]


def _preview_png(name: str, key: str) -> bytes:
    """The preview from disk, rendered once per name and layout. Renders are seeded by the name, so a name
    always looks the same; concurrent misses both render and the atomic rename keeps one."""
    root = _st().cfg.data_dir / "previews"
    d = root / LAYOUT
    path = d / f"{key}.png"
    try:
        return path.read_bytes()
    except OSError:
        pass
    buf = BytesIO()
    render_badge(name, SAMPLE_TAGLINE, 3, rng=random.Random(name)).save(buf, "PNG")
    data = buf.getvalue()
    tmp = d / f".{key}.{threading.get_ident()}.tmp"
    try:  # best effort: a full or read-only disk still serves the freshly rendered preview
        if not d.is_dir():  # first use of this layout: older layouts' previews are dead
            d.mkdir(parents=True, exist_ok=True)
            for old in root.iterdir():
                if old != d and old.is_dir():
                    shutil.rmtree(old, ignore_errors=True)
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except OSError:
        log.warning("preview cache write failed in %s", d, exc_info=True)
        tmp.unlink(missing_ok=True)
    return data


@bp.get("/preview/<name_id>.png")
def preview(name_id):
    n = next((n for n in _st().content.names if n.id == name_id), None)
    if n is None:
        abort(404)
    key = preview_key(n.name)
    cache = PREVIEW_IMMUTABLE if request.args.get("v") == key else "private, max-age=300"
    return Response(_preview_png(n.name, key), mimetype="image/png", headers={"Cache-Control": cache})


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


@bp.post("/test-coloring")
def test_coloring():
    st = _st()
    page = pick_page(st.rng, st.cfg.coloring_path)
    if page is None:
        return jsonify(error=f"no readable coloring page in {st.cfg.coloring_path}: add PNG/JPG files"), 409
    return _print(page)


@bp.post("/provider")
def provider():
    st, name = _st(), _body().get("name")
    try:
        p = get_provider(name, st.cfg)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    st.provider, st.provider_name = p, name  # runtime only; .env stays the default
    return jsonify(ok=True)
