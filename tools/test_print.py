"""Hardware smoke test: python tools/test_print.py "Lil' Gnomesayer" [printer-target] [--qr URL]

Target defaults to $PRINTER, else usb://0x04f9:0x209b. Use "file" to write a PNG to out/.
"""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.badge import render_badge  # noqa: E402
from app.printer import make_printer  # noqa: E402

TAGLINE = "Dusty since the womb"

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("name", nargs="?", default="Test Burner")
    ap.add_argument("target", nargs="?", default=os.environ.get("PRINTER", "usb://0x04f9:0x209b"))
    ap.add_argument("--qr", metavar="URL", default=None)
    a = ap.parse_args()
    img = render_badge(a.name, TAGLINE, first_burn=3, qr_url=a.qr)
    make_printer(a.target, ROOT / "out")(img)
    print(f"sent {a.name!r} to {a.target}")
