import random

import pytest
from PIL import ImageChops

from app import badge
from app.badge import BORDERS, NAME_BOX, NAME_MIN_PT, fit_name, render_badge
from app.printer import shift

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
    s = badge.SAFE_INSET_PX
    for box in [(0, s, w, s + 30), (0, h - s - 30, w, h - s), (s, 0, s + 30, h), (w - s - 30, 0, w - s, h)]:
        assert has_ink(img, box), (style, box)


@pytest.mark.parametrize("style", BORDERS)
@pytest.mark.parametrize("name,qr", [("Gnome", None), (LONG, QR)])
def test_ink_stays_inside_safe_inset_and_survives_shift(style, name, qr):
    img = render(border=style, name=name, qr_url=qr)
    s = badge.SAFE_INSET_PX
    inv = ImageChops.invert(img.convert("L"))
    x0, y0, x1, y1 = inv.getbbox()
    assert x0 >= s and y0 >= s and x1 <= badge.W - s and y1 <= badge.H - s
    for dx in (-s, s):
        for dy in (-s, s):
            assert shift(img, dx, dy).convert("L").histogram()[0] == img.convert("L").histogram()[0], (dx, dy)


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


@pytest.mark.parametrize("name", ["Gnomeschooled", "Gnomeschooled Glitter Baby", "Bring Your Own Baby Bottle",
                                  "Supercalifragilistic Dustbunny"])
def test_words_are_never_split_when_a_smaller_size_fits(name):
    pt, lines = fit_name(name)
    assert " ".join(lines).split() == name.split()  # every word intact, none broken mid-word
    font = badge.name_font(pt)
    assert max(font.getlength(line) for line in lines) <= NAME_BOX[2] - NAME_BOX[0]


def test_hyphenated_name_breaks_after_a_hyphen_instead_of_shrinking():
    name = "Glitter-Encrusted-Supernova"
    pt, lines = fit_name(name)
    assert "".join(lines) == name and len(lines) == 2 and lines[0].endswith("-")
    assert pt >= 14  # treating the compound as one unbreakable word would force it down to ~10 pt


def test_tagline_keeps_words_whole():
    box = (0, 0, 500, 200)
    pt, lines = badge._fit("Certified dust goblin, Gnomeschooled", badge.TEXT_FONT, 8, 20, box, 2)
    assert " ".join(lines).split() == "Certified dust goblin, Gnomeschooled".split()


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


def box_ink(img, bare, box):
    return ink_bbox(img.crop(box), bare.crop(box))


@pytest.mark.parametrize("qr", [None, QR])
@pytest.mark.parametrize("tagline", ["Radically self-reliant (mostly)", "W" * 32, "Q" * 60])
@pytest.mark.parametrize("name", ["Gnome", LONG, "W" * 40])
def test_tagline_clears_first_burn_line(name, tagline, qr):
    bare = render(name="", tagline="", first_burn=2, qr_url=qr)
    nothing = render(name="", tagline="", first_burn=None, qr_url=qr)
    fb = ink_bbox(bare, nothing)
    assert fb
    zone = (fb[0], fb[1] - 8, fb[2], fb[3])  # first-burn ink plus an 8 px clearance above it
    img = render(name=name, tagline=tagline, first_burn=2, qr_url=qr)
    assert box_ink(img, bare, zone) is None


def test_overlong_text_is_truncated_inside_its_boxes():
    name, tagline = "W" * 80, "Q" * 60
    bare = render(name="", tagline="", first_burn=None, qr_url=QR)
    img = render(name=name, tagline=tagline, first_burn=None, qr_url=QR)
    x0, y0, x1, y1 = NAME_BOX
    right = badge.QR_BOX[0] - 20
    # nothing drawn outside the union of the name box and the tagline box (left of the QR)
    tag_bottom = badge.H - badge.MARGIN_PX
    for outside in [(0, 0, badge.W, y0), (0, 0, x0, badge.H), (x1, 0, badge.W, badge.H - 1),
                    (right, y1, badge.QR_BOX[0], tag_bottom)]:
        outside = tuple(max(0, v) for v in outside)
        # frame band excluded: compare only the interior
        inner = (max(outside[0], 30), max(outside[1], 30), min(outside[2], badge.W - 30), min(outside[3], badge.H - 30))
        if inner[0] < inner[2] and inner[1] < inner[3]:
            assert box_ink(img, bare, inner) is None, inner
    pt, lines = fit_name(name)
    assert len(lines) <= badge.NAME_MAX_LINES and lines[-1].endswith("…")


def test_tagline_box_bottom_clears_first_burn_ink():
    font = badge._font(badge.TEXT_FONT, badge.FIRST_BURN_PT)
    text = badge.first_burn_text(2)
    ink_top = badge.H - badge.MARGIN_PX + font.getbbox(text, anchor="ms")[1]
    assert badge.tagline_bottom(2) < ink_top
    assert badge.tagline_bottom(None) == badge.H - badge.MARGIN_PX
