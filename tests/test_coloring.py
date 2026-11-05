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


def framed(size):
    """A solid black image: its ink box is exactly where pick_page placed it."""
    return Image.new("RGB", size, "black")


@pytest.mark.parametrize("size", [(3000, 1000), (800, 2400)])
def test_pick_page_fits_wide_and_tall_inside_inset(tmp_path, size):
    framed(size).save(tmp_path / "page.png")
    img = coloring.pick_page(random.Random(1), tmp_path)
    assert img is not None and img.size == (W, H) and img.mode == "1"
    box = ink_box(img)
    assert box and inside_inset(box)
    x0, y0, x1, y1 = box
    wide = size[0] / size[1] > 1
    assert (x1 - x0 == W - 2 * SAFE_INSET_PX) if wide else (y1 - y0 == H - 2 * SAFE_INSET_PX)
    assert abs((x1 - x0) / (y1 - y0) - size[0] / size[1]) < 0.02  # aspect kept


def test_pick_page_thresholds_without_dither(tmp_path):
    src = Image.new("L", (W, H), 160)  # light grey left half
    src.paste(90, (W // 2, 0, W, H))  # dark grey right half
    src.save(tmp_path / "greys.png")
    img = coloring.pick_page(random.Random(), tmp_path)
    assert img is not None
    gray = img.convert("L")
    mid_y = H // 2
    # light grey -> all white, dark grey -> solid ink: no dither speckle on either side
    assert gray.crop((60, mid_y - 50, W // 2 - 5, mid_y + 50)).getextrema() == (255, 255)
    assert gray.crop((W // 2 + 5, mid_y - 50, W - 60, mid_y + 50)).getextrema() == (0, 0)


def test_pick_page_transparent_background_is_white(tmp_path):
    img = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    img.paste((0, 0, 0, 255), (40, 40, 60, 60))
    img.save(tmp_path / "clear.png")
    page = coloring.pick_page(random.Random(), tmp_path)
    assert page is not None
    box = ink_box(page)
    assert box and box[2] - box[0] < W / 3  # only the square is ink, not the transparent background


def test_pick_page_blank_image_is_none(tmp_path, caplog):
    Image.new("L", (300, 200), 255).save(tmp_path / "blank.png")
    assert coloring.pick_page(random.Random(), tmp_path) is None
    assert any("blank.png" in r.message for r in caplog.records)


def test_pick_page_applies_exif_orientation(tmp_path):
    exif = Image.Exif()
    exif[0x0112] = 6  # stored landscape, shown rotated 90 degrees: a portrait phone photo
    Image.new("L", (400, 200), 0).save(tmp_path / "phone.jpg", exif=exif)
    page = coloring.pick_page(random.Random(), tmp_path)
    box = page and ink_box(page)
    assert box
    x0, y0, x1, y1 = box
    assert y1 - y0 > x1 - x0  # drawn portrait, as the phone showed it


def test_pick_page_rejects_huge_images(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(coloring, "MAX_PIXELS", 100 * 100)
    framed((101, 100)).save(tmp_path / "huge.png")
    assert coloring.pick_page(random.Random(), tmp_path) is None
    assert any("huge.png" in r.message for r in caplog.records)
    framed((100, 100)).save(tmp_path / "huge.png")
    assert coloring.pick_page(random.Random(), tmp_path) is not None


def test_pool_listing_error_means_empty_pool(tmp_path, monkeypatch, caplog):
    framed((10, 10)).save(tmp_path / "a.png")

    def denied(self):
        raise PermissionError("denied")

    monkeypatch.setattr(type(tmp_path), "iterdir", denied)
    assert coloring.page_files(tmp_path) == []
    assert coloring.pick_page(random.Random(), tmp_path) is None
    assert any("denied" in r.message and r.levelname == "WARNING" for r in caplog.records)


def test_pick_page_ignores_non_images_and_missing_dirs(tmp_path):
    (tmp_path / "notes.txt").write_text("hi")
    (tmp_path / "page.gif").write_bytes(b"GIF89a")
    assert coloring.page_files(tmp_path) == []
    assert coloring.pick_page(random.Random(), tmp_path) is None
    assert coloring.pick_page(random.Random(), tmp_path / "nope") is None
    framed((10, 10)).save(tmp_path / "a.PNG")
    assert [p.name for p in coloring.page_files(tmp_path)] == ["a.PNG"]


def test_pick_page_unreadable_file_is_none_and_warns_once(tmp_path, caplog):
    (tmp_path / "broken.png").write_bytes(b"not a png")
    assert coloring.pick_page(random.Random(), tmp_path) is None
    assert coloring.pick_page(random.Random(), tmp_path) is None
    assert [r.levelname for r in caplog.records if "broken.png" in r.message] == ["WARNING"]


def test_empty_pool_warns_once(tmp_path, caplog):
    for _ in range(3):
        assert coloring.pick_page(random.Random(), tmp_path / "empty") is None
    assert len([r for r in caplog.records if "inactive" in r.message]) == 1


@pytest.mark.parametrize("size", [(5000, 1), (20000, 2)])
def test_pick_page_degenerate_sizes_are_unusable(tmp_path, size):
    framed(size).save(tmp_path / "sliver.png")
    assert coloring.pick_page(random.Random(), tmp_path) is None


def test_hidden_files_are_not_pages(tmp_path):
    framed((10, 10)).save(tmp_path / "._page.png")
    framed((10, 10)).save(tmp_path / ".hidden.png")
    framed((10, 10)).save(tmp_path / "page.png")
    assert [p.name for p in coloring.page_files(tmp_path)] == ["page.png"]


def test_bad_pick_retries_another_file(tmp_path):
    for n in ("a.png", "b.png"):
        (tmp_path / n).write_bytes(b"not a png")
    framed((10, 10)).save(tmp_path / "c.png")
    assert all(coloring.pick_page(random.Random(seed), tmp_path) is not None for seed in range(20))


def test_threshold_matches_badge_at_gray_128(tmp_path):
    src = Image.new("L", (W, H), 0)
    src.paste(128, (W // 2, 0, W, H))
    src.save(tmp_path / "g.png")
    page = coloring.pick_page(random.Random(), tmp_path)
    assert page is not None
    want = Image.new("L", (1, 1), 128).convert("1", dither=Image.Dither.NONE).getpixel((0, 0))
    assert page.getpixel((W - 100, H // 2)) == want


def test_pool_warning_repeats_after_rate_limit(tmp_path, monkeypatch, caplog):
    now = [0.0]
    monkeypatch.setattr(coloring.time, "monotonic", lambda: now[0])
    for t in (1e6, 1e6 + 1, 1e6 + coloring.WARN_EVERY_S + 1):
        now[0] = t
        coloring.pick_page(random.Random(), tmp_path / "gone")
    assert len([r for r in caplog.records if "inactive" in r.message]) == 2
