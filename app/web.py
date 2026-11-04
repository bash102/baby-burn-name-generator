"""Kiosk app: Flask factory, guest API, and the `python -m app.web` entry point."""
import argparse
import io
import logging
import random
import socket
import threading
import time
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

import segno
from flask import Flask, Response, jsonify, redirect, request

from app.admin import bp as admin_bp
from app.badge import render_badge
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
    recent_badges: deque = field(default_factory=lambda: deque(maxlen=50))  # (id, name_id, display, tagline, first_burn, photo_url)
    rng: random.Random = field(default_factory=random.SystemRandom)

    def reload_content(self) -> None:
        try:
            content = load_content(self.cfg.content_dir)
        except ContentError as e:
            self.content_error = str(e)
            log.error("content reload failed, keeping the old content:\n%s", e)
            return
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
    st = AppState(cfg, content, names, printer or make_printer(cfg.printer, cfg.data_dir / "prints"),
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
        s = sessions.create(photo or None, st.provider, st.content.tag_vocab)
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
            fut = cloud.delete(s.token)
            photos.delete(s.token)
            try:  # privacy: only say ok once the cloud copy is really gone
                fut.result(timeout=CLOUD_WAIT_S)
            except Exception:
                log.warning("cloud delete not confirmed for %s", s.token[:6] + "…")
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
    tags, first_burn = set(), None
    fb, pool = _served(st.content)
    for q in ([fb] if fb else []) + pool:
        for o in q.options:
            if o.id in chosen.get(q.id, ()):
                tags |= o.tags
                if q.fixed == "first_burn" and first_burn is None:
                    first_burn = o.first_burn
    f = s.tag_future
    if f and f.done() and not f.exception():  # costume tags only if ready; never wait
        tags |= f.result()

    name_id, display = st.names.pick(tags, st.rng)
    lines = st.content.approved_taglines
    tagline = st.rng.choice(lines).text if lines else ""
    photo_url = st.cloud.url(s.token) if s.token else None
    img = render_badge(display, tagline, first_burn, qr_url=photo_url)
    try:
        st.printer(img)
        printed = True
    except Exception:  # any printer fault (USB, OSError) must not lose the name
        log.exception("print failed for %s", display)
        printed = False
    st.recent_badges.append((uuid.uuid4().hex, name_id, display, tagline, first_burn, photo_url))
    s.name_id = name_id
    return {"name": display, "tagline": tagline,
            "first_burn_label": f"First Burn: Baby Burn {first_burn}" if first_burn is not None else None,
            "printed": printed, "photo_url": photo_url, "retention_days": RETENTION_DAYS}


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
    if args.dev:
        serve(app, "127.0.0.1", args.port)
    else:
        cfg = app.extensions["babyburn"].cfg
        for f in (cfg.tls_cert, cfg.tls_key):
            if not f.is_file():
                raise SystemExit(f"TLS file not found: {f} (run deploy/setup-pi.sh or set TLS_CERT/TLS_KEY)")
        serve(app, "0.0.0.0", cfg.listen_port, (str(cfg.tls_cert), str(cfg.tls_key)))


def serve(app: Flask, host: str, port: int, ssl_context=None) -> None:
    # ceiling: the Werkzeug dev server does the TLS handshake in its single accept thread, so one stalled
    # client froze every request; a socket timeout bounds that stall to SOCKET_TIMEOUT_S. Upgrade: gunicorn
    # with gthread workers (handshakes off the accept path) if more clients or a busier network appear.
    socket.setdefaulttimeout(SOCKET_TIMEOUT_S)
    app.run(host, port, threaded=True, ssl_context=ssl_context)


if __name__ == "__main__":
    main()
