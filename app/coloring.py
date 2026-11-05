"""Coloring-page bonus labels: a random owner-reviewed image from the pool in COLORING_DIR
(default <CONTENT_DIR>/coloring)."""
import logging
import random
import time
from pathlib import Path

from PIL import Image, ImageOps

from app.badge import H, SAFE_INSET_PX, W

PAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}
MAX_PIXELS = 40_000_000  # bigger source images are refused before decoding (memory on the Pi)
TRIES = 3  # distinct files tried before a guest goes without the bonus
WARN_EVERY_S = 600  # a recurring pool problem is logged again after this long, not once per guest
log = logging.getLogger(__name__)
_last_warned: dict[str, float] = {}


def _warn(msg: str) -> None:
    now = time.monotonic()
    if now - _last_warned.get(msg, -WARN_EVERY_S) >= WARN_EVERY_S:
        _last_warned[msg] = now
        log.warning(msg)


def page_files(coloring_dir: Path) -> list[Path]:
    """The pool: PNG/JPEG files in coloring_dir, not hidden (none if it is missing or unlistable), sorted."""
    try:
        if not coloring_dir.is_dir():
            return []
        return sorted(p for p in coloring_dir.iterdir()
                      if p.suffix.lower() in PAGE_SUFFIXES and not p.name.startswith(".") and p.is_file())
    except OSError as e:
        _warn(f"cannot list coloring pages in {coloring_dir}: {e}")
        return []


def _fit(path: Path) -> Image.Image:
    """The page at path on the label canvas, 1-bit; raises ValueError when it is unusable."""
    with Image.open(path) as src:
        if src.width * src.height > MAX_PIXELS:
            raise ValueError(f"over {MAX_PIXELS} pixels")
        src.draft("L", (W, H))  # JPEG: decode at a reduced scale; other formats ignore it
        rgba = ImageOps.exif_transpose(src).convert("RGBA")  # phone photos come out upright
    flat = Image.new("RGB", rgba.size, "white")
    flat.paste(rgba, mask=rgba)  # transparent background prints as white
    page = ImageOps.contain(flat.convert("L"), (W - 2 * SAFE_INSET_PX, H - 2 * SAFE_INSET_PX))
    img = Image.new("L", (W, H), "white")
    img.paste(page, ((W - page.width) // 2, (H - page.height) // 2))
    out = img.convert("1", dither=Image.Dither.NONE)  # same threshold as the badge
    if out.getextrema()[0]:  # no black pixel at all
        raise ValueError("blank after thresholding")
    return out


def pick_page(rng: random.Random, coloring_dir: Path) -> Image.Image | None:
    """Return a random pool page scaled to fit inside SAFE_INSET_PX (aspect kept, white margins) as a 1-bit
    image, or None when the pool is empty or the picked file is unusable (no bonus)."""
    files = page_files(coloring_dir)
    if not files:
        _warn(f"no coloring pages in {coloring_dir}: the coloring bonus is inactive")
        return None
    for path in rng.sample(files, min(TRIES, len(files))):
        try:
            return _fit(path)
        except Exception as e:  # corrupt, odd-sized, huge or blank: try another, never fail the badge
            _warn(f"coloring page {path} is unusable, skipping it: {e}")
    return None
