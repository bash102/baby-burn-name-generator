"""Hardware smoke test: python tools/test_print.py "Lil' Gnomesayer" [printer-target] [--qr URL] [--offset-x N] [--offset-y N]

Target defaults to $PRINTER, else usb://0x04f9:0x209b. Use "file" to write a PNG to out/.
Offsets default to BADGE_OFFSET_X/Y from the environment (range -OFFSET_LIMIT..OFFSET_LIMIT).
"""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.badge import render_badge  # noqa: E402
from app.config import OFFSET_LIMIT, Config  # noqa: E402
from app.printer import make_printer  # noqa: E402

TAGLINE = "Dusty since the womb"


def _offset(s: str) -> int:
    v = int(s)
    if abs(v) > OFFSET_LIMIT:
        raise argparse.ArgumentTypeError(f"must be between -{OFFSET_LIMIT} and {OFFSET_LIMIT}, got {v}")
    return v


def main(argv=None) -> None:
    cfg = Config.from_env()
    ap = argparse.ArgumentParser()
    ap.add_argument("name", nargs="?", default="Test Burner")
    ap.add_argument("target", nargs="?", default=os.environ.get("PRINTER", "usb://0x04f9:0x209b"))
    ap.add_argument("--qr", metavar="URL", default=None)
    ap.add_argument("--offset-x", type=_offset, default=cfg.badge_offset_x, help="dots, +right (default: BADGE_OFFSET_X)")
    ap.add_argument("--offset-y", type=_offset, default=cfg.badge_offset_y, help="dots, +down (default: BADGE_OFFSET_Y)")
    a = ap.parse_args(argv)
    img = render_badge(a.name, TAGLINE, first_burn=3, qr_url=a.qr)
    make_printer(a.target, ROOT / "out", (a.offset_x, a.offset_y))(img)
    print(f"sent {a.name!r} to {a.target} with offset x={a.offset_x} y={a.offset_y}")


if __name__ == "__main__":
    main()
