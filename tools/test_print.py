"""Hardware smoke test: python tools/test_print.py "Lil' Gnomesayer" [printer-target]

Target defaults to $PRINTER, else usb://0x04f9:0x209b. Use "file" to write a PNG to out/.
"""
import os
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.printer import make_printer  # noqa: E402

W, H = 954, 672  # landscape badge; the printer wrapper rotates it


def plain_badge(name: str) -> Image.Image:
    img = Image.new("1", (W, H), 1)
    try:
        font = ImageFont.load_default(size=96)
    except TypeError:  # very old Pillow
        font = ImageFont.load_default()
    ImageDraw.Draw(img).text((W // 2, H // 2), name, fill=0, font=font, anchor="mm")
    return img


def render(name: str) -> Image.Image:
    try:
        from app.badge import render_badge  # Task 3; may not exist yet
    except ImportError:
        return plain_badge(name)
    return render_badge(name)


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "Test Burner"
    target = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("PRINTER", "usb://0x04f9:0x209b")
    make_printer(target, ROOT / "out")(render(name))
    print(f"sent {name!r} to {target}")
