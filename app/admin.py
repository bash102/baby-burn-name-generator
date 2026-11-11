"""PIN-protected owner pages: dashboard, content review/add, reprint. HTTP Basic, password = ADMIN_PIN."""
import csv
import hashlib
import hmac
import io
import json
import logging
import os
import pwd
import random
import re
import shutil
import subprocess
import threading
import time
from collections import Counter
from io import BytesIO
from pathlib import Path
from urllib.parse import quote, urlparse

from flask import Blueprint, Response, abort, current_app, jsonify, make_response, redirect, render_template, request

from app import badge
from app.badge import render_badge
from app.coloring import page_files, pick_page
from app.config import PROVIDERS
from app.content import (KINDS, MAX_NAME, MAX_OPTION, MAX_PROMPT, MAX_QUIP, MAX_TAGLINE, STATUSES, ContentError,
                         append_entry, set_status, update_entry)
from app.names import MATCH_LOG_COLS
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


@bp.context_processor
def _limits():
    """The Add and Edit forms' maxlength values: the same caps the content validator enforces."""
    return {"limits": {"max_name": MAX_NAME, "max_tagline": MAX_TAGLINE, "max_option": MAX_OPTION,
                       "max_quip": MAX_QUIP, "max_prompt": MAX_PROMPT}}


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
        providers=sorted(PROVIDERS),
        album_url=st.cloud.album_url(), album_count=len(st.cloud.album.entries()),
        updates=_updates(), coloring_pool=len(page_files(st.cfg.coloring_path)), sample_banner=st.cfg.content_is_sample and SAMPLE_BANNER, msg=request.args.get("msg"),
        tuning=_tuning(st.names.match_rows()[-10:][::-1], {n.id: n.name for n in st.content.names}))


def _tuning(rows: list[dict], names: dict[str, str]) -> list[dict]:
    """The dashboard's last guests, newest first, from match_log rows; top3 stores ids, shown as names."""
    return [{"looks": json.loads(r["looks"]), "words": json.loads(r["answer_words"]), "tags": json.loads(r["tags"]),
             "name": r["display"], "score": r["score"],
             "alts": [(names.get(a["name_id"], a["name_id"]), a["score"]) for a in json.loads(r["top3"] or "[]")]}
            for r in rows]


SUPPLY_LOW = 2  # a detected look word with this few unused names left is flagged


def _pct(n: int, total: int) -> int:
    return round(100 * n / total) if total else 0


def matching_stats(rows: list[dict], looks, supply: dict[str, int]) -> dict:
    """The /admin/matching numbers from match_log rows (JSON columns as text) in one pass.
    `supply` is unused approved names per word."""
    decided, states, hist, seen, answers = Counter(), Counter(), Counter(), Counter(), Counter()
    for r in rows:
        decided[r["decided_by"]] += 1
        states[r["photo_state"]] += 1
        hist[r["score"]] += 1
        seen.update(json.loads(r["looks"]))
        answers.update(json.loads(r["answer_words"]))
    total, photo_guests = len(rows), len(rows) - states["none"]
    return {
        "total": total, "photo_guests": photo_guests,
        "decided": [(k, n, _pct(n, total)) for k, n in decided.most_common()],
        "photo_state": [(k, n, _pct(n, total)) for k, n in states.most_common()],
        "histogram": sorted(hist.items()),
        "looks": [(w, n, _pct(n, photo_guests)) for w, n in seen.most_common()],
        "answers": answers.most_common(),
        "supply_demand": sorted(((w, seen[w], supply.get(w, 0), seen[w] > 0 and supply.get(w, 0) <= SUPPLY_LOW)
                                 for w in set(looks) | set(seen)), key=lambda x: (-x[1], x[0])),
        "never": sorted(set(looks) - set(seen)),
        "recent": [{**r, **{k: ", ".join(json.loads(r[k])) for k in ("looks", "answer_words", "tags")}}
                   for r in rows[-20:][::-1]],
    }


@bp.get("/matching")
def matching():
    st = _st()
    stats = matching_stats(st.names.match_rows(), st.content.looks, st.names.remaining_by_word())
    return render_template("admin.html", page="matching", st=st, m=stats)


def _cell(v):
    """A CSV cell a spreadsheet will not run as a formula (names are owner-written, but still)."""
    return "'" + v if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r") else v


@bp.get("/matching.csv")
def matching_csv():
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(MATCH_LOG_COLS)
    w.writerows([_cell(r[c]) for c in MATCH_LOG_COLS] for r in _st().names.match_rows())
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=match_log.csv"})


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
        sample_banner=st.cfg.content_is_sample and SAMPLE_BANNER, preview_key=preview_key, edit_data=edit_data)


def edit_data(kind: str, r) -> dict:
    """A review row's editable values: the Edit form's prefill, and /admin/edit's reply for repainting the row."""
    if kind == "names":
        return {"name": r.name, "tags": sorted(r.tags), "words": sorted(r.words), "key": preview_key(r.name)}
    if kind == "taglines":
        return {"text": r.text}
    return {"prompt": r.prompt, "type": r.type, "quip": r.quip, "fixed": bool(r.fixed),
            "options": [{"id": o.id, "text": o.text, "correct": o.correct, "tags": sorted(o.tags),
                         "words": sorted(o.words)} for o in r.options]}


@bp.post("/edit")
def edit():
    d, st = _body(), _st()
    kind, id_ = d.get("kind"), d.get("id")
    err = _write(lambda: update_entry(st.cfg.content_dir, kind, id_, d.get("fields")))
    if err:
        return jsonify(error=err), 400
    row = next(r for r in getattr(st.content, kind) if r.id == id_)
    return jsonify(ok=True, row=edit_data(kind, row))


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
        e = {"id": _slug(name, taken), "name": name, "tags": _tags(f.get("tags", "")),
             "status": "approved", "needs_owner": False, "note": ""}
        if words := _tags(f.get("words", "")):
            e["words"] = words
        return e
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
            if words := _tags(f.get(f"words_{s}", "")):
                o["words"] = words
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


def _print(img, on_ok=None):
    try:
        _st().printer(img)
    except Exception as e:  # any printer fault (PrintError, USB OSError) is a JSON error, never a 500
        log.exception("admin print failed")
        return jsonify(error=str(e) or type(e).__name__), 502
    if on_ok:
        try:
            on_ok()
        except Exception:  # the label printed: never report a failure that invites a second print
            log.exception("after-print step failed")
    return jsonify(ok=True)


@bp.get("/reprints")
def reprints():
    st, q = _st(), request.args.get("q", "")
    return render_template("admin.html", page="reprints", st=st, q=q, badges=st.names.badges(q))


@bp.get("/photos")
def photos():
    st = _st()
    return render_template("admin.html", page="photos", st=st, entries=st.cloud.album.entries(),
                           album_base=st.cloud.album_url().rsplit("/", 1)[0])


TOKEN = re.compile(r"[A-Za-z0-9_-]{22}")  # secrets.token_urlsafe(16), the photo URL's last segment


@bp.post("/photo-delete")
def photo_delete():
    st, token = _st(), _body().get("token")
    if not isinstance(token, str) or not TOKEN.fullmatch(token):
        return jsonify(error="token must be a photo token"), 400
    # ponytail: full badge scan; fine for the hundreds a party makes.
    if token not in dict(st.cloud.album.entries()) and not any(
            b["photo_url"] == st.cloud.url(token) for b in st.names.badges()):
        return jsonify(error="no such photo"), 404
    if not st.delete_photo(token):
        return jsonify(error="cloud delete not confirmed; try again"), 502
    return jsonify(ok=True)


@bp.post("/reprint")
def reprint():
    id_ = _body().get("id")
    names = _st().names
    b = names.badge(id_) if isinstance(id_, str) else None
    if b is None:
        return jsonify(error="no such badge"), 404
    img = render_badge(b["display"], b["tagline"], b["first_burn"], qr_url=b["photo_url"], rng=random.Random(b["id"]))
    return _print(img, on_ok=lambda: names.mark_printed(id_))


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
