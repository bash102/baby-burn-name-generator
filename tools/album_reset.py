"""Empty the shared album (e.g. after rehearsal): delete every album/<ALBUM_SECRET>/ object, clear the
album index, mark those guests' photo pages deleted, and re-put an empty album page. Asks first.

Usage: uv run --python 3.11 --env-file .env python tools/album_reset.py   (with the kiosk stopped)
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Config  # noqa: E402
from app.photos import AlbumIndex, album_page_key, make_s3_client, put_album_page, put_html, render_photo_page  # noqa: E402


def _keys(s3, bucket: str, prefix: str) -> list[str]:
    keys, token = [], None
    while True:
        res = s3.list_objects_v2(Bucket=bucket, Prefix=prefix, **({"ContinuationToken": token} if token else {}))
        keys += [o["Key"] for o in res.get("Contents", [])]
        if not res.get("IsTruncated"):
            return keys
        token = res["NextContinuationToken"]


def run(cfg: Config, s3, album: AlbumIndex, ask=input, say=print) -> int:
    prefix, bucket = f"album/{cfg.album_secret}/", cfg.photo_bucket
    keys, entries = _keys(s3, bucket, prefix), album.entries()
    say(f"This deletes {len(keys)} objects under {prefix} and {len(entries)} album entries.")
    if ask("Type yes to empty the album: ").strip() != "yes":
        say("Aborted, nothing deleted.")
        return 0
    for i in range(0, len(keys), 1000):  # DeleteObjects takes at most 1000 keys
        s3.delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": k} for k in keys[i:i + 1000]], "Quiet": True})
    album_url = f"{cfg.photo_base_url}/{album_page_key(cfg.album_secret)}"
    for token, _ in entries:
        album.remove(token)
        put_html(s3, bucket, f"p/{token}", render_photo_page(
            token, state="deleted", has_dusty=False, uploaded_at=datetime.now(timezone.utc), album_url=album_url))
    put_album_page(s3, bucket, cfg.photo_base_url, cfg.album_secret, [])
    say(f"Album emptied: {len(keys)} objects deleted.")
    return len(keys)


if __name__ == "__main__":
    cfg = Config.from_env()
    if not (cfg.photo_bucket and cfg.photo_base_url and cfg.album_secret and cfg.s3_access_key_id):
        sys.exit("PHOTO_BUCKET, PHOTO_BASE_URL, ALBUM_SECRET and S3_* must be set (see docs/ops.md)")
    run(cfg, make_s3_client(cfg), AlbumIndex(cfg.data_dir / "album.sqlite3"))
