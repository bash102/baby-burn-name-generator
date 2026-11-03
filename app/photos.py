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

IMAGES = ("original", "dusty")
HTML = "text/html; charset=utf-8"


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

    def delete(self, token: str) -> None:
        shutil.rmtree(self._dir(token), ignore_errors=True)
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
  if (p) p.outerHTML = "<p class=status>This photo self-destructed.</p>";
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
  el.textContent = ms > 0
    ? "This photo self-destructs in " + plural(Math.floor(ms / 864e5), "day") + ", "
      + plural(Math.floor(ms % 864e5 / 36e5), "hour") + ", on or after " + when
    : "This photo self-destructs any moment now (on or after " + when + ")";
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
  else document.getElementById("developing").textContent = "The dusty baby didn't make it. Your original is below.";
};
probe.src = DUSTY;
"""


def render_photo_page(token: str, *, state: Literal["temp", "album", "deleted"], has_dusty: bool,
                      uploaded_at: datetime, retention_days: int = 14, album_url: str) -> str:
    """Self-contained mobile page at p/<token>: deletion status first, then the images."""
    album_link = f"<p><a href='{html.escape(album_url)}'>See the Baby Burn album</a></p>"
    if state == "deleted":
        return _doc("Baby Burn photo", f"<p class=status>This photo was deleted.</p>{album_link}")
    if state == "album":
        status, exp = "Shared to the Baby Burn album. No expiry.", None
        src = {k: urljoin(album_url, f"{token}/{k}.jpg") for k in IMAGES}
    else:
        exp = uploaded_at + timedelta(days=retention_days)
        status = (f"This photo self-destructs in {_plural(retention_days, 'day')}, 0 hours, "
                  f"on or after {_short_date(exp)}")
        src = {k: f"../tmp/{token}/{k}.jpg" for k in IMAGES}
    src = {k: html.escape(v) for k, v in src.items()}
    dusty = (f"<img src='{src['dusty']}' alt='Your dusty baby'>"
             f"<a class=dl href='{src['dusty']}' download>Download dusty baby</a>" if has_dusty
             else "<p id=developing class=note>Your dusty baby is still developing…</p>")
    body = (f"<p id=status class=status>{html.escape(status)}</p>"
            f"<div id=photos>{dusty}"
            f"<img src='{src['original']}' alt='Your original photo'>"
            f"<a class=dl href='{src['original']}' download>Download original</a></div>{album_link}")
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
    return _doc("Baby Burn 3 album", f"{style}<h1>Baby Burn 3 album</h1><div class=grid>{grid}</div>")


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

    def copy_object(self, *, Bucket, Key, CopySource, **kw):
        self.objects[Key] = dict(self.objects[CopySource["Key"]])

    def delete_objects(self, *, Bucket, Delete):
        for o in Delete["Objects"]:
            self.objects.pop(o["Key"], None)

    def list_objects_v2(self, *, Bucket, Prefix="", **kw):
        return {"Contents": [{"Key": k} for k in sorted(self.objects) if k.startswith(Prefix)], "IsTruncated": False}


def album_page_key(secret: str) -> str:
    return f"album/{secret}/index.html"


def put_html(client, bucket: str, key: str, page: str) -> None:
    client.put_object(Bucket=bucket, Key=key, Body=page.encode(), ContentType=HTML, CacheControl="no-cache")


def put_album_page(client, bucket: str, base_url: str, secret: str, entries: list[tuple[str, str]]) -> None:
    put_html(client, bucket, album_page_key(secret), render_album_page(entries, f"{base_url}/album/{secret}"))


class CloudPhotos:
    """Bucket layout: p/<token> (page, no expiry), tmp/<token>/*.jpg (14-day lifecycle),
    album/<secret>/<token>/*.jpg + album/<secret>/index.html (no expiry). All ops are async;
    ops on one token run in submission order. Failures are logged and counted in `errors`."""

    def __init__(self, client, bucket: str, base_url: str, album_secret: str, album: AlbumIndex, executor,
                 now=_now, retention_days: int = 14):
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

    def _run(self, token: str, fn) -> Future:
        def task():
            if prev:
                wait([prev])
            try:
                fn(self._st.setdefault(token, {"state": "temp", "uploaded_at": self.now(),
                                               "original": False, "dusty": False}))
            except Exception:
                with self._lock:
                    self.errors += 1
                log.exception("cloud op failed for %s", token[:6] + "…")
                raise

        with self._lock:
            prev = self._last.get(token)
            fut = self._last[token] = self.executor.submit(task)
        return fut

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
                                 retention_days=self.retention_days, album_url=self.album_url())
        put_html(self.client, self.bucket, f"p/{token}", page)

    def _put_album_page(self) -> None:
        put_album_page(self.client, self.bucket, self.base_url, self.secret, self.album.entries())

    def upload(self, token: str, *, original: bytes | None = None, dusty: bytes | None = None) -> Future:
        def op(st):
            if st["state"] == "deleted":
                return
            try:
                for kind, data in (("original", original), ("dusty", dusty)):
                    if data is not None:
                        self.client.put_object(
                            Bucket=self.bucket, Key=self._key(token, st, kind), Body=data, ContentType="image/jpeg",
                            ContentDisposition=f'attachment; filename="baby-burn-{kind}.jpg"')
                        st[kind] = True
            finally:  # the page goes up even if an image failed, so the QR never 404s
                self._put_page(token, st)

        return self._run(token, op)

    def share(self, token: str, playa_name: str) -> Future:
        def op(st):
            if st["state"] != "temp":
                return
            tmp = [self._key(token, st, k) for k in IMAGES if st[k]]
            for src in tmp:
                self.client.copy_object(Bucket=self.bucket, Key=f"album/{self.secret}/{src[4:]}",
                                        CopySource={"Bucket": self.bucket, "Key": src})
            st["state"] = "album"
            self.album.add(token, playa_name)
            self._put_page(token, st)
            self._put_album_page()
            if tmp:
                self.client.delete_objects(Bucket=self.bucket, Delete={"Objects": [{"Key": k} for k in tmp], "Quiet": True})

        return self._run(token, op)

    def delete(self, token: str) -> Future:
        def op(st):
            st["state"] = "deleted"
            keys = [f"{p}/{token}/{k}.jpg" for p in ("tmp", f"album/{self.secret}") for k in IMAGES]
            self.client.delete_objects(Bucket=self.bucket, Delete={"Objects": [{"Key": k} for k in keys], "Quiet": True})
            self.album.remove(token)
            self._put_page(token, st)
            self._put_album_page()

        return self._run(token, op)
