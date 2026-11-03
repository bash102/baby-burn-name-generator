"""Reset the kiosk for the party after rehearsal: forget every given name (data/names.sqlite3), empty the
shared album (tools/album_reset.py) and clear leftover session photos (data/photos/). Asks first.

Run ON THE PI with the kiosk stopped (the album index lives in data/ there):
  sudo systemctl stop babyburn
  uv run --env-file .env python tools/reset_event.py
  sudo systemctl start babyburn
"""
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Config  # noqa: E402
from app.photos import AlbumIndex, make_s3_client  # noqa: E402
from tools import album_reset  # noqa: E402


def run(cfg: Config, s3, album: AlbumIndex, ask=input, say=print) -> bool:
    data = cfg.data_dir
    say(f"This forgets every given name ({data / 'names.sqlite3'}), empties the shared album "
        f"and clears {data / 'photos'}/.")
    if ask("Type yes to reset the event: ").strip() != "yes":
        say("Aborted, nothing changed.")
        return False
    album_reset.run(cfg, s3, album, ask=lambda _: "yes", say=say)
    for f in data.glob("names.sqlite3*"):  # plus any -journal/-wal left by a crash
        f.unlink()
    shutil.rmtree(data / "photos", ignore_errors=True)
    say("Event reset. Start the kiosk: sudo systemctl start babyburn")
    return True


if __name__ == "__main__":
    cfg = Config.from_env()
    if not (cfg.photo_bucket and cfg.photo_base_url and cfg.album_secret and cfg.s3_access_key_id):
        sys.exit("PHOTO_BUCKET, PHOTO_BASE_URL, ALBUM_SECRET and S3_* must be set (see docs/ops.md)")
    run(cfg, make_s3_client(cfg), AlbumIndex(cfg.data_dir / "album.sqlite3"))
