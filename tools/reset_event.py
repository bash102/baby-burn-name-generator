"""Reset the kiosk for the party after rehearsal: forget every given name and the match log (data/names.sqlite3), empty the
shared album (tools/album_reset.py) and clear leftover session photos (data/photos/). Asks first.

Run ON THE PI with the kiosk stopped (the album index lives in data/ there). The unit is `babyburn` on
setup-pi installs (settings in .env) and `app-babyburn` on git-deploy installs (~/.config/baby-burn/env):
  sudo systemctl stop babyburn
  uv run --env-file .env python tools/reset_event.py
  sudo systemctl start babyburn
"""
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Config  # noqa: E402
from app.photos import AlbumIndex, make_s3_client  # noqa: E402
from tools import album_reset  # noqa: E402


KIOSK_UNITS = ("babyburn", "app-babyburn")  # setup-pi, git-deploy


def running_units(run=subprocess.run) -> list[str]:
    """The kiosk units systemd reports active; none where there is no systemctl (a Mac)."""
    try:
        return [u for u in KIOSK_UNITS
                if run(["systemctl", "is-active", "--quiet", u], stdin=subprocess.DEVNULL).returncode == 0]
    except OSError:
        return []


def run(cfg: Config, s3, album: AlbumIndex, ask=input, say=print, running=running_units) -> bool:
    if active := running():
        say(f"The kiosk is running ({', '.join(active)}); it would rewrite what this deletes. "
            f"Stop it first: sudo systemctl stop {' '.join(active)}")
        return False
    data = cfg.data_dir
    say(f"This forgets every given name and the match log ({data / 'names.sqlite3'}), empties the shared album "
        f"and clears {data / 'photos'}/.")
    if ask("Type yes to reset the event: ").strip() != "yes":
        say("Aborted, nothing changed.")
        return False
    album_reset.run(cfg, s3, album, ask=lambda _: "yes", say=say)
    for f in data.glob("names.sqlite3*"):  # plus any -journal/-wal left by a crash
        f.unlink()
    shutil.rmtree(data / "photos", ignore_errors=True)
    say("Event reset. Start the kiosk again: sudo systemctl start babyburn (or app-babyburn)")
    return True


if __name__ == "__main__":
    cfg = Config.from_env()
    if not (cfg.photo_bucket and cfg.photo_base_url and cfg.album_secret and cfg.s3_access_key_id):
        sys.exit("PHOTO_BUCKET, PHOTO_BASE_URL, ALBUM_SECRET and S3_* must be set (see docs/ops.md)")
    run(cfg, make_s3_client(cfg), AlbumIndex(cfg.data_dir / "album.sqlite3"))
