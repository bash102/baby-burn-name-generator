"""Kiosk app: Flask factory, guest API, and the `python -m app.web` entry point."""
import argparse
import io
import logging
import random
import socket
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

import segno
from flask import Flask, Response, jsonify, redirect, request
from werkzeug.serving import make_server

from app.admin import bp as admin_bp
from app.badge import render_badge
from app.coloring import pick_page
from app.config import Config
from app.content import Content, ContentError, load_content
from app.names import NameStore
from app.photos import RETENTION_DAYS, AlbumIndex, CloudPhotos, MemoryS3, PhotoStore, make_s3_client
from app.printer import make_printer
from app.sessions import Session, SessionStore
from app.transform import Provider, get_provider

log = logging.getLogger(__name__)
CLOUD_WAIT_S = 10
SOCKET_TIMEOUT_S = 10  # seconds a client may stall a socket (TLS handshake, slow upload)
CHOICES = {"album", "delete", "done"}
DEV_CLOUD = "/cloud"  # dev-only route serving the in-memory bucket when PHOTO_BUCKET is unset


@dataclass
class AppState:
    cfg: Config
    content: Content
    names: NameStore
    printer: Callable
    provider_name: str
    provider: Provider
    sessions: SessionStore
    photos: PhotoStore
    cloud: CloudPhotos
    content_error: str | None = None
    rng: random.Random = field(default_factory=random.SystemRandom)

    def delete_photo(self, token: str) -> bool:
        """A guest's Delete (or the owner's admin Remove): both cloud images, the photo page (now "deleted"),
        the album entry and page, local copies and album backups. True once the cloud copy is confirmed gone."""
        fut = self.cloud.delete(token)
        self.photos.delete(token)
        try:  # privacy: only say ok once the cloud copy is really gone
            fut.result(timeout=CLOUD_WAIT_S)
            return True
        except Exception:
            log.warning("cloud delete not confirmed for %s", token[:6] + "…")
            return False

    def reload_content(self) -> None:
        try:
            content = load_content(self.cfg.content_dir)
        except ContentError as e:
            self.content_error = str(e)
            log.error("content reload failed, keeping the old content:\n%s", e)
            return
        self.install_content(content)

    def install_content(self, content: Content) -> None:
        """Serve validated content: the name pool, quiz and admin pages all read from here."""
        self.names.set_pool(content.approved_names)
        self.content, self.content_error = content, None


def _served(c: Content):
    """(first-burn question or None, random pool): exactly the approved questions the quiz can serve."""
    return c.first_burn_question, c.approved_questions


def _expired():
    return jsonify(error="expired"), 404


def create_app(cfg: Config, *, printer=None, provider=None, now=time.monotonic, s3=None) -> Flask:
    app = Flask(__name__, static_folder="static", static_url_path="")
    app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024

    content = load_content(cfg.content_dir)  # fail fast at boot; reloads keep the old content
    names = NameStore(cfg.data_dir / "names.sqlite3")
    names.set_pool(content.approved_names)
    photos = PhotoStore(cfg.data_dir)
    if s3 is None and not cfg.photo_bucket:
        log.warning("PHOTO_BUCKET is unset: photos go to an in-memory bucket served at %s/ (dev only)", DEV_CLOUD)
        s3 = MemoryS3()
    dev_bucket = isinstance(s3, MemoryS3) and not cfg.photo_bucket
    if not dev_bucket:
        if not cfg.photo_base_url or "example.com" in cfg.photo_base_url:
            raise ValueError("PHOTO_BASE_URL must be the bucket's real public URL when PHOTO_BUCKET is set")
        if cfg.album_secret == "change-me" or len(cfg.album_secret) < 16:
            raise ValueError("ALBUM_SECRET must be a random string of 16+ characters when PHOTO_BUCKET is set")
    base_url = DEV_CLOUD if dev_bucket else cfg.photo_base_url
    cloud = CloudPhotos(s3 or make_s3_client(cfg), cfg.photo_bucket, base_url, cfg.album_secret or "dev",
                        AlbumIndex(cfg.data_dir / "album.sqlite3"), ThreadPoolExecutor(4, "cloud"))

    def on_photo(s: Session) -> None:  # before the transform starts, so dusty never lands first
        photos.save_original(s.token, s.photo)
        cloud.upload(s.token, original=s.photo)

    def on_dusty(s: Session, jpeg: bytes) -> None:
        if s.deleted:  # guest deleted it (Delete, Start over, idle) while the transform ran: keep nothing
            return  # (upload() and save_dusty() also skip deleted tokens, for a delete that lands mid-hook)
        try:  # a local-disk hiccup must not fail the guest's transform
            photos.save_dusty(s.token, jpeg)
            if s.choice == "album":
                photos.copy_to_album_backup(s.token, s.name_id)
        except Exception:
            log.exception("local dusty save failed")
        cloud.upload(s.token, dusty=jpeg)

    def on_expire(s: Session) -> None:
        if not s.token:
            return
        photos.discard(s.token)
        if s.result is None:  # abandoned before the name: the guest never saw the QR, so drop the cloud copy
            cloud.delete(s.token)

    sessions = SessionStore(ThreadPoolExecutor(8, "transform"), cfg.session_ttl_s, now,
                            on_photo=on_photo, on_dusty=on_dusty, on_expire=on_expire)
    st = AppState(cfg, content, names, printer or make_printer(cfg.printer, cfg.data_dir / "prints", (cfg.badge_offset_x, cfg.badge_offset_y)),
                  cfg.transform_provider, provider or get_provider(cfg.transform_provider, cfg),
                  sessions, photos, cloud)
    app.extensions["babyburn"] = st
    app.register_blueprint(admin_bp)

    @app.get("/")
    def index():
        return redirect("/kiosk.html")

    @app.post("/api/session")
    def new_session():
        f = request.files.get("photo")
        photo = f.read() if f else None
        if photo and not photo.startswith(b"\xff\xd8"):
            return jsonify(error="photo must be a JPEG"), 400
        s = sessions.create(photo or None, st.provider, st.content.tag_vocab, st.content.looks)
        return jsonify(session_id=s.id, has_photo=s.token is not None)

    @app.get("/api/quiz")
    def quiz():
        c = st.content
        fb, pool = _served(c)
        qs = ([fb] if fb else []) + st.rng.sample(pool, min(cfg.quiz_random_count, len(pool)))
        return jsonify(retention_days=RETENTION_DAYS, questions=[{
            "id": q.id, "type": q.type, "prompt": q.prompt, "quip": q.quip,
            "options": [{"id": o.id, "text": o.text, "correct": o.correct, "first_burn": o.first_burn}
                        for o in q.options],
        } for q in qs])

    @app.post("/api/session/<sid>/finish")
    def finish(sid):
        s = sessions.get(sid)
        if not s:
            return _expired()
        chosen = _parse_answers(request.get_json(silent=True))
        if chosen is None:
            return jsonify(error="answers must be a list of {question_id, option_ids: [str]}"), 400
        with s.lock:
            if s.result is None:
                s.result = _finish(st, s, chosen)
                s.finished_at = now()
        return jsonify(s.result)

    @app.get("/api/session/<sid>/qr.svg")
    def qr(sid):
        s = sessions.get(sid)
        if not s:
            return _expired()
        if not s.token:
            return jsonify(error="no photo"), 404
        buf = io.BytesIO()
        segno.make(cloud.url(s.token), error="m").save(buf, kind="svg", scale=10, border=2)
        return Response(buf.getvalue(), mimetype="image/svg+xml")

    @app.get("/api/session/<sid>/photo")
    def photo(sid):
        s = sessions.get(sid)
        if not s:
            return _expired()
        if s.deleted:
            return jsonify(error="deleted"), 404
        f = s.transform_future
        if f is None:
            return jsonify(status="failed")
        if f.done():
            if f.exception():
                return jsonify(status="failed")
            return Response(f.result(), mimetype="image/jpeg")
        if now() - s.created_at > cfg.transform_timeout_s:
            return jsonify(status="failed")
        return jsonify(status="pending"), 202

    @app.post("/api/session/<sid>/photo-choice")
    def photo_choice(sid):
        s = sessions.get(sid)
        if not s:
            return _expired()
        choice = (request.get_json(silent=True) or {}).get("choice")
        if choice not in CHOICES:
            return jsonify(error=f"choice must be one of {sorted(CHOICES)}"), 400
        if choice == "album" and not s.result:
            return jsonify(error="finish first"), 400
        if s.token and choice == "album":
            if s.deleted:
                return jsonify(ok=False), 409
            fut = cloud.share(s.token, s.result["name"])
            try:
                shared = fut.result(timeout=CLOUD_WAIT_S)
            except Exception:
                shared = None  # not confirmed yet; it stays queued
            if shared is False:  # the cloud copy was already deleted
                return jsonify(ok=False), 409
            s.choice = choice  # before the backup copy, so a dusty landing now is backed up too
            photos.copy_to_album_backup(s.token, s.name_id)
            if shared is None:
                log.warning("album share not confirmed for %s", s.token[:6] + "…")
                return jsonify(ok=False), 502
            return jsonify(ok=True)
        s.choice = choice
        if s.token and choice == "delete":
            s.deleted, s.photo, s.transform_future, s.tag_future = True, None, None, None
            if not st.delete_photo(s.token):
                return jsonify(ok=False), 502
        return jsonify(ok=True)

    if dev_bucket:
        @app.get(f"{DEV_CLOUD}/<path:key>")
        def dev_cloud(key):
            obj = s3.objects.get(key)
            if not obj:
                return "not found", 404
            return Response(obj["Body"], content_type=obj.get("ContentType"))

    return app


def _parse_answers(body) -> dict[str, set[str]] | None:
    """{question_id: option ids} from a /finish body, or None if malformed. A missing `answers` is none given."""
    if not isinstance(body, dict):
        return None if body is not None else {}
    answers, chosen = body.get("answers", []), {}
    if not isinstance(answers, list):
        return None
    for a in answers:
        if not isinstance(a, dict):
            return None
        qid, opts = a.get("question_id"), a.get("option_ids")
        if (not isinstance(qid, (str, int)) or isinstance(qid, bool) or not isinstance(opts, list)
                or not all(isinstance(o, str) for o in opts)):
            return None
        chosen[str(qid)] = set(opts)
    return chosen


def _finish(st: AppState, s: Session, chosen: dict[str, set[str]]) -> dict:
    tags, words, looks, first_burn = set(), set(), set(), None
    fb, pool = _served(st.content)
    for q in ([fb] if fb else []) + pool:
        for o in q.options:
            if o.id in chosen.get(q.id, ()):
                tags |= o.tags
                words |= o.words
                if q.fixed == "first_burn" and first_burn is None:
                    first_burn = o.first_burn
    f, photo_state = s.tag_future, "none"
    if f:  # costume tags and looks only if ready; never wait
        if not f.done():
            photo_state = "not_ready"
        elif f.exception():
            photo_state = "failed"
        else:
            photo_tags, looks = f.result()
            tags |= photo_tags
            photo_state = "ready"

    name_id, display = st.names.pick(tags, st.rng, looks=looks, words=words, photo_state=photo_state)
    lines = st.content.approved_taglines
    tagline = st.rng.choice(lines).text if lines else ""
    photo_url = st.cloud.url(s.token) if s.token else None
    badge_id = uuid.uuid4().hex
    img = render_badge(display, tagline, first_burn, qr_url=photo_url, rng=random.Random(badge_id))  # reprints match
    try:
        st.printer(img)
        printed = True
    except Exception:  # any printer fault (USB, OSError) must not lose the name
        log.exception("print failed for %s", display)
        printed = False
    # Bonus label, only after a printed badge. /finish runs this once per session (the result is cached),
    # so a kiosk retry never prints a second page.
    odds = st.cfg.coloring_odds
    coloring = printed and odds > 0 and st.rng.randrange(odds) == 0 and _print_coloring(st)
    try:  # reprint log only: a DB fault must not cost the guest their name
        st.names.add_badge(badge_id, name_id, display, tagline, first_burn, photo_url, printed)
    except Exception:
        log.exception("badge log write failed for %s", display)
    s.name_id = name_id
    return {"name": display, "tagline": tagline,
            "first_burn_label": f"First Burn: Baby Burn {first_burn}" if first_burn is not None else None,
            "printed": printed, "coloring": coloring, "photo_url": photo_url, "retention_days": RETENTION_DAYS}


def _print_coloring(st: AppState) -> bool:
    # ponytail: badge and page lock separately; an admin test print can land between them. One lock across both if a second kiosk is ever added.
    try:
        page = pick_page(st.rng, st.cfg.coloring_path)
        if page is None:  # empty pool or unusable file: no bonus (pick_page logs it)
            return False
        st.printer(page)
        return True
    except Exception:  # the badge already printed; a bonus fault must not fail the guest or reprint the badge
        log.exception("coloring page print failed")
        return False


def _expire_loop(sessions: SessionStore) -> None:
    while True:
        time.sleep(60)
        try:
            sessions.expire()
        except Exception:
            log.exception("session expiry failed")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description="Baby Burn kiosk")
    ap.add_argument("--dev", action="store_true", help="plain HTTP on 127.0.0.1:8000, no certs")
    ap.add_argument("--port", type=int, default=8000, help="port for --dev")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = create_app(Config.from_env())
    threading.Thread(target=_expire_loop, args=(app.extensions["babyburn"].sessions,), daemon=True).start()
    cfg = app.extensions["babyburn"].cfg
    if args.dev:
        _bind(app, "127.0.0.1", args.port).serve_forever()
        return
    for f in (cfg.tls_cert, cfg.tls_key):
        if not f.is_file():
            raise SystemExit(f"TLS file not found: {f} (run deploy/setup-pi.sh or set TLS_CERT/TLS_KEY)")
    tls = _bind(app, "0.0.0.0", cfg.listen_port, (str(cfg.tls_cert), str(cfg.tls_key)))
    if cfg.local_http_port:  # only once the iPad port is up, so git-deploy's health check can't pass without it
        local = _bind(app, "127.0.0.1", cfg.local_http_port)
        threading.Thread(target=_serve_local, args=(local,), daemon=True).start()
    tls.serve_forever()


def _bind(app: Flask, host: str, port: int, ssl_context=None):
    """Bind (and load the TLS cert) now; a failure exits non-zero instead of leaving a half-up kiosk."""
    # ceiling: the Werkzeug dev server does the TLS handshake in its single accept thread, so one stalled
    # client froze every request; a socket timeout bounds that stall to SOCKET_TIMEOUT_S. Upgrade: gunicorn
    # with gthread workers (handshakes off the accept path) if more clients or a busier network appear.
    socket.setdefaulttimeout(SOCKET_TIMEOUT_S)
    try:
        srv = make_server(host, port, app, threaded=True, ssl_context=ssl_context)
    except (OSError, SystemExit) as e:  # werkzeug calls sys.exit(1) when bind() fails; bad certs raise SSLError
        log.critical("cannot listen on %s:%s: %s", host, port, e)
        raise SystemExit(f"cannot listen on {host}:{port}: {e}") from None
    log.info("listening on %s://%s:%s", "https" if ssl_context else "http", host, port)
    return srv


def _serve_local(srv) -> None:
    try:
        srv.serve_forever()
    except BaseException:
        log.critical("local HTTP listener on %s:%s died; git-deploy health checks will fail", srv.host, srv.port)
        raise


if __name__ == "__main__":
    main()
