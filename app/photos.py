"""Guest photos: local session copies, the cloud bucket (pages + images), and the shared album."""
import html
import json
import logging
import shutil
import sqlite3
import threading
from concurrent.futures import Future, wait
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import urljoin

log = logging.getLogger(__name__)

RETENTION_DAYS = 14  # single source: unshared photos expire this long after upload (also the R2 lifecycle rule)
EVENT_NAME = "Baby Burn 3"

IMAGES = ("original", "dusty")
ALBUM_CHAIN = "album"  # CloudPhotos chain key for album-page writes; tokens are 22 chars, never this
HTML = "text/html; charset=utf-8"
SAVE_TIP = "Tip: press and hold the photo → Save to Photos"


def _now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


# ---------- local ----------

class PhotoStore:
    """Session photos on the Pi (deleted at session end), plus a local backup of album photos."""

    def __init__(self, data_dir: Path):
        self.root = Path(data_dir) / "photos"
        self.album_dir = Path(data_dir) / "album"

    def _dir(self, token: str) -> Path:
        return self.root / token

    def save_original(self, token: str, jpeg: bytes) -> None:
        self._dir(token).mkdir(parents=True, exist_ok=True)
        (self._dir(token) / "original.jpg").write_bytes(jpeg)

    def save_dusty(self, token: str, jpeg: bytes) -> None:
        if self._dir(token).is_dir():  # gone = session ended or guest deleted it
            (self._dir(token) / "dusty.jpg").write_bytes(jpeg)

    def paths(self, token: str) -> tuple[Path, Path | None]:
        dusty = self._dir(token) / "dusty.jpg"
        return self._dir(token) / "original.jpg", dusty if dusty.exists() else None

    def copy_to_album_backup(self, token: str, name_id: str) -> None:
        self.album_dir.mkdir(parents=True, exist_ok=True)
        for kind in IMAGES:
            src = self._dir(token) / f"{kind}.jpg"
            if src.exists():
                shutil.copyfile(src, self.album_dir / f"{name_id}-{token}-{kind}.jpg")

    def discard(self, token: str) -> None:
        """Session end: drop the session copies; album backups stay."""
        shutil.rmtree(self._dir(token), ignore_errors=True)

    def delete(self, token: str) -> None:
        """The guest chose delete: session copies and album backups."""
        self.discard(token)
        for f in self.album_dir.glob(f"*-{token}-*.jpg"):
            f.unlink(missing_ok=True)


class AlbumIndex:
    """Which tokens are in the shared album; the source of truth for the album page."""

    def __init__(self, db_path: Path):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock, self._db:
            self._db.execute("CREATE TABLE IF NOT EXISTS album (token TEXT PRIMARY KEY, playa_name TEXT, added_at TEXT)")

    def add(self, token: str, playa_name: str) -> None:
        with self._lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO album VALUES (?,?,?)", (token, playa_name, _now().isoformat()))

    def remove(self, token: str) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM album WHERE token = ?", (token,))

    def entries(self) -> list[tuple[str, str]]:
        with self._lock:
            return list(self._db.execute("SELECT token, playa_name FROM album ORDER BY added_at, token"))


# ---------- pages ----------

_STYLE = """
body{margin:0;font:18px/1.4 system-ui,sans-serif;background:#f6ead7;color:#3a2a1a;text-align:center}
main{max-width:640px;margin:auto;padding:16px}
.status{font-size:28px;font-weight:800;margin:12px 0 20px}
img{max-width:100%;border-radius:12px;display:block;margin:0 auto 8px}
.dl{display:inline-block;margin:0 0 24px;padding:12px 22px;border-radius:999px;background:#c2410c;color:#fff;
text-decoration:none;font-weight:700}
.note{color:#7a5a3a}
a{color:#9a3412}
"""


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _short_date(d: datetime) -> str:
    return f"{d:%a %b} {d.day}"


def _doc(title: str, body: str, script: str = "") -> str:
    return (f"<!doctype html><html lang=en><head><meta charset=utf-8>"
            f"<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{html.escape(title)}</title><style>{_STYLE}</style></head>"
            f"<body><main>{body}</main>{f'<script>{script}</script>' if script else ''}</body></html>")


# Pieces of the photo page script; only the ones a state needs are included.
_GONE_JS = """
const EXP = new Date(%(exp)s);
function gone() {
  const p = document.getElementById("photos");
  if (p) p.outerHTML = "<p class=status>This photo has been deleted.</p>";
}
document.querySelectorAll("#photos img").forEach(i => i.addEventListener("error", () => {
  if (Date.now() > EXP) gone();
}));
"""
_COUNTDOWN_JS = """
const plural = (n, w) => n + " " + w + (n === 1 ? "" : "s");
const when = EXP.toLocaleDateString(undefined, {weekday: "short", month: "short", day: "numeric"});
function tick() {
  const ms = EXP - Date.now(), el = document.getElementById("status");
  el.textContent = ms >= 36e5
    ? "This photo will be deleted around " + when + " (in about " + plural(Math.max(1, Math.round(ms / 864e5)), "day")
      + "). Download it before then."
    : "This photo is about to be deleted. Download it now if you want it.";
}
tick(); setInterval(tick, 60000);
"""
_DEVELOPING_JS = """
const DUSTY = %(dusty)s, probe = new Image();
let tries = 0;
probe.onload = () => {
  document.getElementById("developing").outerHTML =
    "<img src='" + DUSTY + "' alt='Your dusty baby'><a class=dl href='" + DUSTY + "' download>Download dusty baby</a>";
};
probe.onerror = () => {
  if (Date.now() > EXP) return gone();
  if (++tries < 40) setTimeout(() => { probe.src = DUSTY + "?t=" + Date.now(); }, 3000);
  else document.getElementById("developing").textContent = "Your dusty baby didn't develop. Your original is below.";
};
probe.src = DUSTY;
"""


def _countdown(exp: datetime, now: datetime) -> str:
    left = exp - now
    if left < timedelta(hours=1):
        return "This photo is about to be deleted. Download it now if you want it."
    days = max(1, round(left.total_seconds() / 86400))
    return (f"This photo will be deleted around {_short_date(exp)} (in about {_plural(days, 'day')}). "
            "Download it before then.")


def render_photo_page(token: str, *, state: Literal["temp", "album", "deleted"], has_dusty: bool,
                      uploaded_at: datetime, retention_days: int = RETENTION_DAYS, album_url: str,
                      now: datetime | None = None) -> str:
    """Self-contained mobile page at p/<token>: deletion status first, then the images."""
    album_link = f"<p><a href='{html.escape(album_url)}'>See the Baby Burn album</a></p>"
    if state == "deleted":
        return _doc("Baby Burn photo", f"<p class=status>This photo has been deleted.</p>{album_link}")
    if state == "album":
        status, exp = "This photo is in the Baby Burn album and stays there.", None
        src = {k: urljoin(album_url, f"{token}/{k}.jpg") for k in IMAGES}
    else:
        exp = uploaded_at + timedelta(days=retention_days)
        status = _countdown(exp, now or _now())
        src = {k: f"../tmp/{token}/{k}.jpg" for k in IMAGES}
    src = {k: html.escape(v) for k, v in src.items()}
    dusty = (f"<img src='{src['dusty']}' alt='Your dusty baby'>"
             f"<a class=dl href='{src['dusty']}' download>Download dusty baby</a>" if has_dusty
             else "<p id=developing class=note>Your dusty baby is still developing…</p>")
    body = (f"<p id=status class=status>{html.escape(status)}</p>"
            f"<div id=photos>{dusty}"
            f"<img src='{src['original']}' alt='Your original photo'>"
            f"<a class=dl href='{src['original']}' download>Download original</a></div>"
            f"<p class=note>{SAVE_TIP}</p>{album_link}")
    # album photos never expire: a far-future EXP keeps the 404 handler inert
    script = _GONE_JS % {"exp": json.dumps(exp.isoformat() if exp else "9999-12-31")}
    if exp:
        script += _COUNTDOWN_JS
    if not has_dusty:  # still developing: retry the dusty image every 3 s for up to 2 min
        script += _DEVELOPING_JS % {"dusty": json.dumps(src["dusty"])}
    return _doc("Your Baby Burn photo", body, script)


def render_album_page(entries: list[tuple[str, str]], base_url: str) -> str:
    """Grid of shared dusty photos; `base_url` is the album prefix URL (…/album/<secret>)."""
    figs = []
    for token, name in entries:
        d, o = (html.escape(f"{base_url}/{token}/{k}.jpg") for k in ("dusty", "original"))
        figs.append(f"<figure><a href='{o}'><img src='{d}' alt='' loading=lazy "
                    f"onerror=\"this.onerror=null;this.src='{o}'\"></a>"
                    f"<figcaption>{html.escape(name)}</figcaption></figure>")
    grid = "".join(figs) or "<p class=note>No photos yet. Be the first!</p>"
    style = ("<style>.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px}"
             "figure{margin:0}figcaption{font-weight:700}</style>")
    title = f"{EVENT_NAME} album"
    return _doc(title, f"{style}<h1>{title}</h1><div class=grid>{grid}</div>")


# ---------- cloud ----------

def make_s3_client(cfg):
    import boto3
    from botocore.config import Config as BotoConfig

    return boto3.client(
        "s3", endpoint_url=cfg.s3_endpoint_url, region_name=cfg.s3_region,
        aws_access_key_id=cfg.s3_access_key_id, aws_secret_access_key=cfg.s3_secret_access_key,
        config=BotoConfig(retries={"mode": "standard", "max_attempts": 5},
                           connect_timeout=5, read_timeout=15),
    )


class MemoryS3:
    """In-memory stand-in for the S3 client: tests, and dev runs with PHOTO_BUCKET unset."""

    def __init__(self):
        self.objects: dict[str, dict] = {}

    def put_object(self, *, Bucket, Key, Body, **kw):
        self.objects[Key] = dict(kw, Body=Body.encode() if isinstance(Body, str) else Body)

    def copy_object(self, *, Bucket, Key, CopySource, MetadataDirective="COPY", **kw):
        src = self.objects[CopySource["Key"]]
        self.objects[Key] = dict(kw, Body=src["Body"]) if MetadataDirective == "REPLACE" else dict(src)

    def delete_objects(self, *, Bucket, Delete):
        for o in Delete["Objects"]:
            self.objects.pop(o["Key"], None)

    def list_objects_v2(self, *, Bucket, Prefix="", **kw):
        return {"Contents": [{"Key": k} for k in sorted(self.objects) if k.startswith(Prefix)], "IsTruncated": False}


def album_page_key(secret: str) -> str:
    return f"album/{secret}/index.html"


def put_html(client, bucket: str, key: str, page: str) -> None:
    client.put_object(Bucket=bucket, Key=key, Body=page.encode(), ContentType=HTML, CacheControl="no-cache")


def delete_keys(client, bucket: str, keys: list[str]) -> None:
    for i in range(0, len(keys), 1000):  # DeleteObjects takes at most 1000 keys
        client.delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": k} for k in keys[i:i + 1000]], "Quiet": True})


def put_album_page(client, bucket: str, base_url: str, secret: str, entries: list[tuple[str, str]]) -> None:
    put_html(client, bucket, album_page_key(secret), render_album_page(entries, f"{base_url}/album/{secret}"))


class CloudPhotos:
    """Bucket layout: p/<token> (page, no expiry), tmp/<token>/*.jpg (RETENTION_DAYS lifecycle),
    album/<secret>/<token>/*.jpg + album/<secret>/index.html (no expiry). All ops are async;
    ops on one token run in submission order. Failures are logged and counted in `errors`."""

    def __init__(self, client, bucket: str, base_url: str, album_secret: str, album: AlbumIndex, executor,
                 now=_now, retention_days: int = RETENTION_DAYS):
        self.client, self.bucket, self.base_url, self.secret = client, bucket, base_url, album_secret
        self.album, self.executor, self.now, self.retention_days = album, executor, now, retention_days
        self.errors = 0
        self._lock = threading.Lock()
        self._last: dict[str, Future] = {}
        self._st: dict[str, dict] = {}  # token -> {state, uploaded_at, original, dusty}

    def url(self, token: str) -> str:
        return f"{self.base_url}/p/{token}"

    def album_url(self) -> str:
        return f"{self.base_url}/{album_page_key(self.secret)}"

    def _chain(self, key: str, fn) -> Future:
        """Run fn after the previous op on `key`; the chain entry is dropped once its last op finishes."""
        me: list[Future] = []

        def task():
            if prev:
                wait([prev])
            try:
                return fn()
            except Exception:
                with self._lock:
                    self.errors += 1
                log.exception("cloud op failed for %s", key[:6] + "…")
                raise
            finally:
                with self._lock:  # _chain holds the lock until `me` is set
                    if self._last.get(key) is me[0]:
                        del self._last[key]

        with self._lock:
            prev = self._last.get(key)
            fut = self._last[key] = self.executor.submit(task)
            me.append(fut)
        return fut

    def _run(self, token: str, fn) -> Future:
        # ceiling: _st is kept for the process lifetime (one small dict per guest); pruning it when the
        # chain drains would forget "album"/"deleted" before a late dusty upload arrives.
        return self._chain(token, lambda: fn(self._st.setdefault(
            token, {"state": "temp", "uploaded_at": self.now(), "original": False, "dusty": False})))

    def _refresh_album(self) -> None:
        """Re-put the album page on its own chain, so writes are serialized and the last one reads
        AlbumIndex after every change that queued it."""
        self._chain(ALBUM_CHAIN, lambda: put_album_page(self.client, self.bucket, self.base_url, self.secret,
                                                         self.album.entries()))

    def drain(self) -> None:
        """Wait for every queued op (tests, shutdown)."""
        while True:
            with self._lock:
                futs = [f for f in self._last.values() if not f.done()]
            if not futs:
                return
            wait(futs)

    def _key(self, token: str, st: dict, kind: str) -> str:
        prefix = f"album/{self.secret}" if st["state"] == "album" else "tmp"
        return f"{prefix}/{token}/{kind}.jpg"

    def _put_page(self, token: str, st: dict) -> None:
        page = render_photo_page(token, state=st["state"], has_dusty=st["dusty"], uploaded_at=st["uploaded_at"],
                                 retention_days=self.retention_days, album_url=self.album_url(), now=self.now())
        put_html(self.client, self.bucket, f"p/{token}", page)

    def upload(self, token: str, *, original: bytes | None = None, dusty: bytes | None = None) -> Future:
        def op(st):
            if st["state"] == "deleted":
                return
            try:
                for kind, data in (("original", original), ("dusty", dusty)):
                    if data is not None:  # personal (tmp/) images download as files; album images open inline
                        save = {} if st["state"] == "album" else {
                            "ContentDisposition": f'attachment; filename="baby-burn-{kind}.jpg"'}
                        self.client.put_object(Bucket=self.bucket, Key=self._key(token, st, kind), Body=data,
                                               ContentType="image/jpeg", **save)
                        st[kind] = True
            finally:  # the page goes up even if an image failed, so the QR never 404s
                self._put_page(token, st)

        return self._run(token, op)

    def share(self, token: str, playa_name: str) -> Future:
        """Future[bool]: True once shared (or already shared), False if the photo was deleted."""
        def op(st):
            if st["state"] != "temp":
                return st["state"] == "album"
            tmp = [self._key(token, st, k) for k in IMAGES if st[k]]
            for src in tmp:  # REPLACE drops the tmp copy's attachment header, so album images open inline
                self.client.copy_object(Bucket=self.bucket, Key=f"album/{self.secret}/{src[4:]}",
                                        CopySource={"Bucket": self.bucket, "Key": src}, MetadataDirective="REPLACE",
                                        ContentType="image/jpeg")
            st["state"] = "album"
            self.album.add(token, playa_name)
            self._put_page(token, st)
            self._refresh_album()
            if tmp:
                delete_keys(self.client, self.bucket, tmp)
            return True

        return self._run(token, op)

    def delete(self, token: str) -> Future:
        def op(st):
            st["state"] = "deleted"
            keys = [f"{p}/{token}/{k}.jpg" for p in ("tmp", f"album/{self.secret}") for k in IMAGES]
            delete_keys(self.client, self.bucket, keys)
            self.album.remove(token)
            self._put_page(token, st)
            self._refresh_album()

        return self._run(token, op)
