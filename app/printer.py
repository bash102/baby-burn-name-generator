"""Brother QL-800 printing (DK-1234 60x86 mm labels), or PNG files for dev."""
import threading
import time
from pathlib import Path
from typing import Callable

from brother_ql.backends.helpers import send
from brother_ql.conversion import convert
from brother_ql.raster import BrotherQLRaster
from PIL import Image

_lock = threading.Lock()  # one print job at a time


def shift(img: Image.Image, dx: int, dy: int) -> Image.Image:
    """Move content by (dx, dy) on a white canvas of the same size; anything pushed off an edge is clipped."""
    if not (dx or dy):
        return img
    out = Image.new(img.mode, img.size, 1 if img.mode == "1" else 255)
    out.paste(img, (dx, dy))
    return out

class PrintError(Exception):
    pass


def _file_printer(out_dir: Path) -> Callable[[Image.Image], None]:
    def print_(img: Image.Image) -> None:
        with _lock:
            out_dir.mkdir(parents=True, exist_ok=True)
            # time_ns keeps names unique when two badges print in the same second
            img.save(out_dir / f"badge-{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 10**9:09d}.png")

    return print_


def _usb_printer(target: str) -> Callable[[Image.Image], None]:
    def print_(img: Image.Image) -> None:
        with _lock:
            try:
                instructions = convert(
                    BrotherQLRaster("QL-800"), [img],
                    label="60x86", rotate="90", threshold=70, dither=False, cut=True,
                )
                result = send(instructions, printer_identifier=target, backend_identifier="pyusb")
            except Exception as e:
                raise PrintError(f"printer error: {e}") from e
            if not result.get("did_print"):
                raise PrintError(f"printer did not confirm print: {result.get('outcome', 'unknown')}")

    return print_


def make_printer(target: str, out_dir: Path, offset: tuple[int, int] = (0, 0)) -> Callable[[Image.Image], None]:
    """Single choke point for every badge print; offset = (dx, dy) calibration in printer dots."""
    if target == "file":
        inner = _file_printer(Path(out_dir))
    elif target.startswith("usb://"):
        inner = _usb_printer(target)
    else:
        raise ValueError(f"unknown printer target: {target!r}")
    return lambda img: inner(shift(img, *offset))
