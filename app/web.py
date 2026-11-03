"""Kiosk app: Flask factory, guest API, and the `python -m app.web` entry point."""
import argparse
import io
import logging
import random
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

import segno
from flask import Flask, Response, jsonify, redirect, request

from app.badge import render_badge
from app.config import ROOT, Config
from app.content import Content, ContentError, load_content
from app.names import NameStore
from app.photos import AlbumIndex, CloudPhotos, MemoryS3, PhotoStore, make_s3_client
from app.printer import PrintError, make_printer
from app.sessions import Session, SessionStore
from app.transform import Provider, get_provider

log = logging.getLogger(__name__)
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
    recent_badges: deque = field(default_factory=lambda: deque(maxlen=50))  # (name_id, display, tagline, first_burn)
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
    if not dev_bucket and not (cfg.photo_base_url and cfg.album_secret):
        raise ValueError("PHOTO_BASE_URL and ALBUM_SECRET must be set when PHOTO_BUCKET is set")
    base_url = DEV_CLOUD if dev_bucket else cfg.photo_base_url
    cloud = CloudPhotos(s3 or make_s3_client(cfg), cfg.photo_bucket, base_url, cfg.album_secret or "dev",
                        AlbumIndex(cfg.data_dir / "album.sqlite3"), ThreadPoolExecutor(4, "cloud"),
                        retention_days=cfg.photo_retention_days)

    def on_photo(s: Session) -> None:  # before the transform starts, so dusty never lands first
        photos.save_original(s.token, s.photo)
        cloud.upload(s.token, original=s.photo)

    def on_dusty(s: Session, jpeg: bytes) -> None:
        photos.save_dusty(s.token, jpeg)
        if s.choice == "album":
            photos.copy_to_album_backup(s.token, s.name_id)
        cloud.upload(s.token, dusty=jpeg)

    sessions = SessionStore(ThreadPoolExecutor(8, "transform"), cfg.session_ttl_s, now,
                            on_photo=on_photo, on_dusty=on_dusty, on_expire=lambda s: s.token and photos.delete(s.token))
    st = AppState(cfg, content, names, printer or make_printer(cfg.printer, cfg.data_dir / "prints"),
                  cfg.transform_provider, provider or get_provider(cfg.transform_provider, cfg),
                  sessions, photos, cloud)
    app.extensions["babyburn"] = st

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
        pool = c.approved_questions
        qs = [c.first_burn_question] + st.rng.sample(pool, min(cfg.quiz_random_count, len(pool)))
        return jsonify(questions=[{
            "id": q.id, "type": q.type, "prompt": q.prompt, "quip": q.quip,
            "options": [{"id": o.id, "text": o.text, "correct": o.correct, "first_burn": o.first_burn}
                        for o in q.options],
        } for q in qs])

    @app.post("/api/session/<sid>/finish")
    def finish(sid):
        s = sessions.get(sid)
        if not s:
            return _expired()
        with s.lock:
            if s.result is None:
                s.result = _finish(st, s, (request.get_json(silent=True) or {}).get("answers"))
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
        s.choice = choice
        if s.token and choice == "album":
            cloud.share(s.token, s.result["name"])
            photos.copy_to_album_backup(s.token, s.name_id)
        elif s.token and choice == "delete":
            cloud.delete(s.token)
            photos.delete(s.token)
        return jsonify(ok=True)

    if dev_bucket:
        @app.get(f"{DEV_CLOUD}/<path:key>")
        def dev_cloud(key):
            obj = s3.objects.get(key)
            if not obj:
                return "not found", 404
            return Response(obj["Body"], content_type=obj.get("ContentType"))

    return app


def _finish(st: AppState, s: Session, answers) -> dict:
    chosen = {}
    for a in answers if isinstance(answers, list) else []:
        if isinstance(a, dict) and isinstance(a.get("option_ids"), list):
            chosen[a.get("question_id")] = set(map(str, a["option_ids"]))
    tags, first_burn = set(), None
    for q in st.content.questions:
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
    except PrintError:
        log.exception("print failed for %s", display)
        printed = False
    st.recent_badges.append((name_id, display, tagline, first_burn))
    s.name_id = name_id
    return {"name": display, "tagline": tagline,
            "first_burn_label": f"First Burn: Baby Burn {first_burn}" if first_burn is not None else None,
            "printed": printed, "photo_url": photo_url}


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
    # ponytail: Flask dev server is fine for one kiosk; switch to gunicorn if more clients appear.
    if args.dev:
        app.run("127.0.0.1", args.port, threaded=True)
    else:
        certs = ROOT / "certs"
        app.run("0.0.0.0", 443, threaded=True,
                ssl_context=(str(certs / "babyburn.local.pem"), str(certs / "babyburn.local-key.pem")))


if __name__ == "__main__":
    main()
