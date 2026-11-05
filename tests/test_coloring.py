import random

import pytest
from PIL import Image, ImageChops

from app import coloring
from app.badge import H, SAFE_INSET_PX, W


def ink_box(img):
    return ImageChops.invert(img.convert("L")).getbbox()


def inside_inset(box):
    x0, y0, x1, y1 = box
    return x0 >= SAFE_INSET_PX and y0 >= SAFE_INSET_PX and x1 <= W - SAFE_INSET_PX and y1 <= H - SAFE_INSET_PX


def test_render_size_mode_and_deterministic():
    img = coloring.render_coloring(5)
    assert img.size == (W, H) and img.mode == "1"
    assert img.tobytes() == coloring.render_coloring(5).tobytes()
    assert img.tobytes() != coloring.render_coloring(6).tobytes()


@pytest.mark.parametrize("seed", range(60))
def test_render_ink_inside_safe_inset(seed):
    assert inside_inset(ink_box(coloring.render_coloring(seed)))


def framed(size):
    """A solid black image: its ink box is exactly where pick_page placed it."""
    return Image.new("RGB", size, "black")


@pytest.mark.parametrize("size", [(3000, 1000), (800, 2400)])
def test_pick_page_fits_wide_and_tall_inside_inset(tmp_path, size):
    framed(size).save(tmp_path / "page.png")
    img = coloring.pick_page(random.Random(1), tmp_path)
    assert img is not None and img.size == (W, H) and img.mode == "1"
    x0, y0, x1, y1 = ink_box(img)
    assert inside_inset((x0, y0, x1, y1))
    wide = size[0] / size[1] > 1
    assert (x1 - x0 == W - 2 * SAFE_INSET_PX) if wide else (y1 - y0 == H - 2 * SAFE_INSET_PX)
    assert abs((x1 - x0) / (y1 - y0) - size[0] / size[1]) < 0.02  # aspect kept


def test_pick_page_thresholds_without_dither(tmp_path):
    Image.new("L", (W, H), 160).save(tmp_path / "light.jpg")
    Image.new("L", (W, H), 90).save(tmp_path / "dark.jpg")
    pages = {coloring.pick_page(random.Random(s), tmp_path).convert("L").getextrema() for s in range(20)}
    assert pages == {(255, 255), (0, 255)}  # light grey -> all white; dark grey -> solid ink, no dither speckle


def test_pick_page_transparent_background_is_white(tmp_path):
    Image.new("RGBA", (100, 100), (0, 0, 0, 0)).save(tmp_path / "clear.png")
    assert ink_box(coloring.pick_page(random.Random(), tmp_path)) is None


def test_pick_page_ignores_non_images_and_missing_dirs(tmp_path):
    (tmp_path / "notes.txt").write_text("hi")
    (tmp_path / "page.gif").write_bytes(b"GIF89a")
    assert coloring.page_files(tmp_path) == []
    assert coloring.pick_page(random.Random(), tmp_path) is None
    assert coloring.pick_page(random.Random(), tmp_path / "nope") is None
    framed((10, 10)).save(tmp_path / "a.PNG")
    assert [p.name for p in coloring.page_files(tmp_path)] == ["a.PNG"]


def test_coloring_page_falls_back_to_drawing(tmp_path):
    img = coloring.coloring_page(random.Random(3), tmp_path / "nope")
    assert img.size == (W, H) and img.mode == "1" and ink_box(img) is not None


def test_coloring_page_falls_back_on_unreadable_file(tmp_path):
    (tmp_path / "broken.png").write_bytes(b"not a png")
    img = coloring.coloring_page(random.Random(3), tmp_path)
    assert img.size == (W, H) and ink_box(img) is not None


def test_coloring_page_prefers_the_pool(tmp_path):
    framed((10, 10)).save(tmp_path / "a.png")
    assert coloring.coloring_page(random.Random(), tmp_path).tobytes() == coloring.pick_page(random.Random(), tmp_path).tobytes()
