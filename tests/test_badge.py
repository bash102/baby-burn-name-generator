import random

import pytest
from PIL import ImageChops

from app import badge
from app.badge import BORDERS, NAME_BOX, NAME_MIN_PT, fit_name, render_badge

LONG = "Captain Moist Towelette of Hole Foods and Friends"[:40]
QR = "https://photos.example.com/p/aaaaaaaaaaaaaaaaaaaaaa"


def render(**kw):
    kw.setdefault("name", "Gnome")
    kw.setdefault("tagline", "Dusty since the womb")
    kw.setdefault("first_burn", 2)
    kw.setdefault("border", "dust_dots")
    kw.setdefault("rng", random.Random(1))
    return render_badge(**kw)


def has_ink(img, box):
    return img.crop(box).convert("L").point(lambda v: 255 - v).getbbox() is not None


def test_size_and_mode():
    img = render()
    assert img.size == (954, 672) and img.mode == "1"


def test_long_name_fits():
    pt, lines = fit_name(LONG)
    assert pt >= NAME_MIN_PT and 1 <= len(lines) <= badge.NAME_MAX_LINES
    font = badge.name_font(pt)
    x0, y0, x1, y1 = NAME_BOX
    assert max(font.getlength(line) for line in lines) <= x1 - x0
    assert len(lines) * badge.line_height(font) <= y1 - y0


def test_short_name_bigger_than_long():
    assert fit_name("Gnome")[0] > fit_name("Captain Moist Towelette of Hole Foods")[0]


@pytest.mark.parametrize("style", BORDERS)
def test_each_border_draws_ink_on_edges(style):
    assert len(BORDERS) >= 3
    img = render(border=style)
    w, h = img.size
    for box in [(0, 0, w, 30), (0, h - 30, w, h), (0, 0, 30, h), (w - 30, 0, w, h)]:
        assert has_ink(img, box), (style, box)


def test_no_first_burn_line_when_none():
    a = render(first_burn=None)
    b = render(first_burn=2)
    assert ImageChops.difference(a.convert("L"), b.convert("L")).getbbox() is not None


def test_qr_drawn_clear_of_name():
    with_qr = render(name=LONG, qr_url=QR)
    without = render(name=LONG, tagline="", first_burn=None)
    assert has_ink(with_qr, badge.QR_BOX) and ink_bbox(with_qr, without)
    qx0, qy0, qx1, qy1 = badge.QR_BOX
    nx0, ny0, nx1, ny1 = NAME_BOX
    assert ny1 <= qy0 or nx1 <= qx0
    assert qx1 - qx0 == pytest.approx(236, abs=12)
    # the QR region does not depend on the name text
    other = render(name="Gnome", qr_url=QR)
    diff = ImageChops.difference(
        with_qr.crop(badge.QR_BOX).convert("L"), other.crop(badge.QR_BOX).convert("L")
    )
    assert diff.getbbox() is None


def ink_bbox(a, b):
    return ImageChops.difference(a.convert("L"), b.convert("L")).getbbox()


@pytest.mark.parametrize("n", [40, 45])
def test_unbreakable_name_still_fits(n):
    name = "W" * n
    pt, lines = fit_name(name)
    font = badge.name_font(pt)
    x0, y0, x1, y1 = NAME_BOX
    assert "".join(lines) == name and len(lines) <= badge.NAME_MAX_LINES
    assert max(font.getlength(line) for line in lines) <= x1 - x0
    bare = render(name="", tagline="", first_burn=None, qr_url=QR)
    bx0, by0, bx1, by1 = ink_bbox(render(name=name, tagline="", first_burn=None, qr_url=QR), bare)
    assert x0 <= bx0 and bx1 <= x1 and y0 <= by0 and by1 <= y1


def test_long_tagline_stays_left_of_qr():
    tag = "Q" * 32
    a = render(tagline=tag, first_burn=None, qr_url=QR)
    b = render(tagline="", first_burn=None, qr_url=QR)
    left = (badge.QR_BOX[0] - 15, badge.QR_BOX[1], badge.W - badge.MARGIN_PX, badge.H - badge.MARGIN_PX)
    assert ink_bbox(a.crop(left), b.crop(left)) is None  # tagline never reaches the QR column
    assert ink_bbox(a, b)


def test_typographic_and_accented_text_renders_and_fits():
    name = "Zoë ’“Dust” – Déjà—Vu"
    pt, lines = fit_name(name)
    font = badge.name_font(pt)
    assert max(font.getlength(line) for line in lines) <= NAME_BOX[2] - NAME_BOX[0]
    img = render(name=name, tagline="It’s “fine” – truly—café")
    assert has_ink(img, NAME_BOX)


def test_same_seed_same_bytes():
    kw = dict(name="Gnome", tagline="Dusty", first_burn=1, border=None, qr_url=QR)
    a = render_badge(rng=random.Random(5), **kw)
    b = render_badge(rng=random.Random(5), **kw)
    assert a.tobytes() == b.tobytes()


def test_short_name_group_is_vertically_centered():
    img = render(name="Hi", tagline="Dust", first_burn=None, border="ticket_notches")
    inner = img.convert("L").point(lambda v: 255 - v).crop((60, 60, 894, 612)).getbbox()
    top, bottom = inner[1], 552 - inner[3]
    assert abs(top - bottom) < 60 and top > 20


@pytest.mark.parametrize("name", ["Gnome", LONG])
def test_tagline_capped_below_name(name):
    pt, _ = fit_name(name)
    cap = badge.tagline_max_pt(pt)
    assert cap < pt
    assert cap <= badge.TAGLINE_NAME_RATIO * pt
    assert cap <= badge.TAGLINE_MAX_PT
