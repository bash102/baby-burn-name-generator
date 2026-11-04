"""Hardware smoke test: python tools/test_print.py "Lil' Gnomesayer" [printer-target] [--qr URL] [--offset-x N] [--offset-y N]

Target defaults to $PRINTER, else usb://0x04f9:0x209b. Use "file" to write a PNG to out/.
"""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.badge import render_badge  # noqa: E402
from app.config import Config  # noqa: E402
from app.printer import make_printer  # noqa: E402

TAGLINE = "Dusty since the womb"

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("name", nargs="?", default="Test Burner")
    ap.add_argument("target", nargs="?", default=os.environ.get("PRINTER", "usb://0x04f9:0x209b"))
    ap.add_argument("--qr", metavar="URL", default=None)
    ap.add_argument("--offset-x", type=int, default=None, help="dots, +right; overrides BADGE_OFFSET_X")
    ap.add_argument("--offset-y", type=int, default=None, help="dots, +down; overrides BADGE_OFFSET_Y")
    a = ap.parse_args()
    cfg = Config.from_env()
    ox = cfg.badge_offset_x if a.offset_x is None else a.offset_x
    oy = cfg.badge_offset_y if a.offset_y is None else a.offset_y
    img = render_badge(a.name, TAGLINE, first_burn=3, qr_url=a.qr)
    make_printer(a.target, ROOT / "out", (ox, oy))(img)
    print(f"sent {a.name!r} to {a.target} with offset x={ox} y={oy}")
