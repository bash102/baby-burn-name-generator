"""Hardware smoke test: python tools/test_print.py "Lil' Gnomesayer" [printer-target] [--qr URL] [--border NAME] [--offset-x N] [--offset-y N] [--coloring]

Target defaults to $PRINTER, else usb://0x04f9:0x209b. Use "file" to write a PNG to out/.
Offsets default to BADGE_OFFSET_X/Y from the environment (range -OFFSET_LIMIT..OFFSET_LIMIT).
--coloring prints a bonus coloring page (from COLORING_DIR, else a drawn one) instead of the badge.
"""
import argparse
import os
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.badge import BORDERS, render_badge  # noqa: E402
from app.coloring import coloring_page  # noqa: E402
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
    ap.add_argument("--border", choices=BORDERS, help="force one border style (default: random)")
    ap.add_argument("--offset-x", type=_offset, default=cfg.badge_offset_x, help="dots, +right (default: BADGE_OFFSET_X)")
    ap.add_argument("--offset-y", type=_offset, default=cfg.badge_offset_y, help="dots, +down (default: BADGE_OFFSET_Y)")
    ap.add_argument("--coloring", action="store_true", help="print a coloring page instead of a badge")
    a = ap.parse_args(argv)
    printer = make_printer(a.target, ROOT / "out", (a.offset_x, a.offset_y))
    if a.coloring:
        printer(coloring_page(random.Random(), cfg.coloring_path))
        print(f"sent a coloring page to {a.target} with offset x={a.offset_x} y={a.offset_y}")
        return
    border = a.border or random.choice(BORDERS)
    img = render_badge(a.name, TAGLINE, first_burn=3, qr_url=a.qr, border=border)
    printer(img)
    print(f"sent {a.name!r} to {a.target} with offset x={a.offset_x} y={a.offset_y}, border={border}")


if __name__ == "__main__":
    main()
