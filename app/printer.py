"""Brother QL-800 printing (DK-1234 60x86 mm labels), or PNG files for dev."""
import logging
import threading
import time
from pathlib import Path
from time import sleep  # module attribute so tests can skip the waits
from typing import Callable

from brother_ql.backends.helpers import send
from brother_ql.conversion import convert
from brother_ql.raster import BrotherQLRaster
from PIL import Image

log = logging.getLogger(__name__)
_lock = threading.Lock()  # one print job at a time
RETRY_WAITS_S = (2, 4)  # USB only: seconds before each retry after a failed attempt (3 attempts in all)


def shift(img: Image.Image, dx: int, dy: int) -> Image.Image:
    """Move content by (dx, dy) on a white canvas of the same size; anything pushed off an edge is clipped."""
    if not (dx or dy):
        return img
    out = Image.new(img.mode, img.size, 1 if img.mode == "1" else "white")
    out.paste(img, (dx, dy))
    return out


class PrintError(Exception):
    def __init__(self, msg: str, retry: bool = False):
        super().__init__(msg)
        self.retry = retry  # True only when the job never reached the printer, so a retry can't make a duplicate


def _file_printer(out_dir: Path) -> Callable[[Image.Image], None]:
    def print_(img: Image.Image) -> None:
        with _lock:
            out_dir.mkdir(parents=True, exist_ok=True)
            # time_ns keeps names unique when two badges print in the same second
            img.save(out_dir / f"badge-{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 10**9:09d}.png")

    return print_


def _usb_once(target: str, img: Image.Image) -> None:
    try:
        instructions = convert(
            BrotherQLRaster("QL-800"), [img],
            label="60x86", rotate="90", threshold=70, dither=False, cut=True,
        )
        result = send(instructions, printer_identifier=target, backend_identifier="pyusb")
    except Exception as e:
        # brother_ql's pyusb backend raises ValueError("Device not found") before writing anything. Any other
        # error (a USB error or timeout) can come from the status read after the job was written, so it may
        # have printed: not retried, an admin can reprint it.
        raise PrintError(f"printer error: {e}", retry=str(e) == "Device not found") from e
    if not result.get("did_print"):
        # "error": the printer reported a fault. Anything else ("sent", "unknown") may have printed without
        # the status arriving in time, so it is not retried.
        outcome = result.get("outcome", "unknown")
        raise PrintError(f"printer did not confirm print: {outcome}", retry=outcome == "error")


def _usb_printer(target: str) -> Callable[[Image.Image], None]:
    def print_(img: Image.Image) -> None:
        with _lock:  # held across retries, so another job can't land between a failed attempt and its retry
            for attempt, wait in enumerate((*RETRY_WAITS_S, None), 1):
                try:
                    return _usb_once(target, img)
                except PrintError as e:
                    log.warning("print attempt %d failed: %s", attempt, e)
                    if not e.retry:
                        raise
                    if wait is None:
                        raise PrintError(f"{e} (after {attempt} attempts)") from e
                    sleep(wait)

    return print_


def make_printer(target: str, out_dir: Path, offset: tuple[int, int] = (0, 0)) -> Callable[[Image.Image], None]:
    """Wrap the printer so every badge is shifted by offset=(dx, dy) printer dots."""
    if target == "file":
        inner = _file_printer(Path(out_dir))
    elif target.startswith("usb://"):
        inner = _usb_printer(target)
    else:
        raise ValueError(f"unknown printer target: {target!r}")
    return lambda img: inner(shift(img, *offset))
