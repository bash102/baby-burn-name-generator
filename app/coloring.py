"""Coloring-page bonus labels: a random owner-reviewed image from the pool in COLORING_DIR."""
import logging
import random
from pathlib import Path

from PIL import Image, ImageOps

from app.badge import H, SAFE_INSET_PX, W

PAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}
THRESHOLD = [0] * 129 + [255] * 127  # L -> "1": ink at <= 128, no dither
log = logging.getLogger(__name__)
_warned: set[str] = set()  # each pool problem is logged once, not once per guest


def _warn_once(msg: str) -> None:
    if msg not in _warned:
        _warned.add(msg)
        log.warning(msg)


def page_files(coloring_dir: Path) -> list[Path]:
    """The pool: PNG/JPEG files in coloring_dir (none if it is missing), in a stable order."""
    if not coloring_dir.is_dir():
        return []
    return sorted(p for p in coloring_dir.iterdir() if p.suffix.lower() in PAGE_SUFFIXES and p.is_file())


def pick_page(rng: random.Random, coloring_dir: Path) -> Image.Image | None:
    """A random pool page fitted (aspect kept, white letterbox) inside SAFE_INSET_PX, mode "1".
    None when the pool is empty or the picked file is unreadable: then there is no bonus."""
    files = page_files(coloring_dir)
    if not files:
        _warn_once(f"no coloring pages in {coloring_dir}: the coloring bonus is inactive")
        return None
    path = rng.choice(files)
    try:
        with Image.open(path) as src:
            rgba = src.convert("RGBA")
    except Exception as e:  # a corrupt file must never fail the guest's badge
        _warn_once(f"coloring page {path} is unreadable, skipping the bonus: {e}")
        return None
    flat = Image.new("RGB", rgba.size, "white")
    flat.paste(rgba, mask=rgba)  # transparent background prints as white
    page = ImageOps.contain(flat.convert("L"), (W - 2 * SAFE_INSET_PX, H - 2 * SAFE_INSET_PX))
    img = Image.new("L", (W, H), "white")
    img.paste(page, ((W - page.width) // 2, (H - page.height) // 2))
    return img.point(THRESHOLD, "1")
