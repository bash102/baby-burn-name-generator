"""Coloring-page bonus labels: a random page from the pool in COLORING_DIR (pre-generated images),
or, when the pool is empty, a drawn page (thick black outlines, white insides, Burning Man baby motifs)."""
import logging
import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from app.badge import H, SAFE_INSET_PX, TEXT_FONT, W, _font

LINE = 6  # outline width in printer dots (~0.5 mm), thick enough to color inside
MARGIN = SAFE_INSET_PX + 20
CAPTION = "Baby Burn coloring bonus"
PAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}
THRESHOLD = [0] * 129 + [255] * 127  # L -> "1": ink at <= 128, no dither
log = logging.getLogger(__name__)


def _poly(d, pts, w=LINE):
    d.polygon(pts, fill="white", outline="black", width=w)


def _oval(d, cx, cy, rx, ry, w=LINE):
    d.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill="white", outline="black", width=w)


def _star_pts(cx, cy, r, points=5, inner=0.45, rot=-math.pi / 2):
    pts = []
    for i in range(points * 2):
        rr = r if i % 2 == 0 else r * inner
        a = rot + i * math.pi / points
        pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    return pts


def _flame_pts(cx, cy, s, tip_dx):
    # Teardrop with wavy sides: right side up to the tip, left side back down, then the
    # round bottom from left to right so the outline never crosses itself.
    def side_pt(side, u):  # u: 0 at the base, 1 at the tip
        width = s * 0.5 * (1 - u ** 1.6) * (1 - 0.3 * u)
        wobble = math.sin(u * math.pi * 3 + side) * s * 0.07 * u
        return (cx + side * (width + wobble) + tip_dx * u ** 2, cy + s * 0.3 - u * s * 1.2)

    right = [side_pt(1, k / 20) for k in range(21)]
    left = [side_pt(-1, k / 20) for k in range(20, -1, -1)]
    bottom = [(cx - s * 0.5 * math.cos(a), cy + s * 0.3 + s * 0.35 * math.sin(a))
              for a in [math.pi * k / 12 for k in range(1, 12)]]
    return right + left + bottom


def flame(d, cx, cy, s, rng):
    tip = rng.uniform(-0.25, 0.25) * s
    _poly(d, _flame_pts(cx, cy, s, tip))
    if s >= 80:  # an inner tongue only where there is room to color it
        _poly(d, _flame_pts(cx, cy + s * 0.22, s * 0.5, tip * 0.5))


def cat(d, cx, cy, s, rng):
    r = s * 0.55
    for side in (-1, 1):  # ears first so the head covers their base
        _poly(d, [(cx + side * r * 0.25, cy - r * 0.75), (cx + side * r * 0.95, cy - r * 0.35),
                  (cx + side * r * 0.85, cy - r * 1.25)])
    _oval(d, cx, cy, r, r * 0.88)
    if rng.random() < 0.6:  # playa goggles
        for side in (-1, 1):
            _oval(d, cx + side * r * 0.38, cy - r * 0.12, r * 0.3, r * 0.24)
        d.line((cx - r * 0.08, cy - r * 0.12, cx + r * 0.08, cy - r * 0.12), fill="black", width=LINE)
        d.line((cx - r * 0.98, cy - r * 0.2, cx - r * 0.68, cy - r * 0.14), fill="black", width=LINE)
        d.line((cx + r * 0.68, cy - r * 0.14, cx + r * 0.98, cy - r * 0.2), fill="black", width=LINE)
    else:
        for side in (-1, 1):
            _oval(d, cx + side * r * 0.36, cy - r * 0.12, r * 0.12, r * 0.16)
    _poly(d, [(cx - r * 0.1, cy + r * 0.2), (cx + r * 0.1, cy + r * 0.2), (cx, cy + r * 0.32)], w=4)
    for side in (-1, 1):
        for k in (-1, 0, 1):
            d.line((cx + side * r * 0.3, cy + r * 0.32 + k * r * 0.08,
                    cx + side * r * 1.1, cy + r * 0.3 + k * r * 0.18), fill="black", width=3)


def planet(d, cx, cy, s, rng):
    r = s * 0.42
    tilt = rng.uniform(-0.35, 0.35)
    ring = [(cx + s * 0.8 * math.cos(a), cy + s * 0.22 * math.sin(a) + s * 0.8 * math.cos(a) * tilt)
            for a in [2 * math.pi * k / 48 for k in range(48)]]
    _poly(d, ring)
    _oval(d, cx, cy, r, r)
    front = [p for k, p in enumerate(ring) if 0 < k < 24]  # lower half of the ring passes in front
    d.line(front, fill="black", width=LINE)
    for _ in range(2):  # craters
        a, rr = rng.uniform(0, 2 * math.pi), rng.uniform(0.2, 0.55) * r
        _oval(d, cx + rr * math.cos(a), cy - abs(rr * math.sin(a)) * 0.6, r * 0.14, r * 0.1, w=4)


def rocket(d, cx, cy, s, rng):
    w, h = s * 0.32, s * 0.95
    for side in (-1, 1):
        _poly(d, [(cx + side * w * 0.9, cy + h * 0.15), (cx + side * w * 1.8, cy + h * 0.6),
                  (cx + side * w * 0.9, cy + h * 0.5)])
    flame(d, cx, cy + h * 0.7, s * 0.5, rng)
    body = [(cx, cy - h)]
    body += [(cx + w * math.sin(a), cy - h + (h * 0.7) * (1 - math.cos(a))) for a in
             [math.pi / 2 * k / 10 for k in range(1, 11)]]
    body += [(cx + w, cy + h * 0.55), (cx - w, cy + h * 0.55)]
    body += [(cx - w * math.sin(a), cy - h + (h * 0.7) * (1 - math.cos(a))) for a in
             [math.pi / 2 * k / 10 for k in range(10, 0, -1)]]
    _poly(d, body)
    _oval(d, cx, cy - h * 0.15, w * 0.5, w * 0.5)


def the_man(d, cx, cy, s, rng):
    # The effigy: diamond head, raised arms, standing on a little pyramid base.
    t = LINE + 2
    base = s * 0.95
    _poly(d, [(cx - s * 0.55, base + cy), (cx + s * 0.55, base + cy), (cx, cy + s * 0.55)])
    d.line((cx, cy - s * 0.45, cx, cy + s * 0.55), fill="black", width=t)
    for side in (-1, 1):
        d.line((cx, cy - s * 0.25, cx + side * s * 0.45, cy - s * 0.6, cx + side * s * 0.55, cy - s * 1.0),
               fill="black", width=t, joint="curve")
        d.line((cx, cy + s * 0.2, cx + side * s * 0.25, cy + s * 0.6), fill="black", width=t)
    _poly(d, [(cx, cy - s * 0.9), (cx + s * 0.17, cy - s * 0.65), (cx, cy - s * 0.42), (cx - s * 0.17, cy - s * 0.65)])
    if rng.random() < 0.5:  # burn night
        for dx in (-0.35, 0, 0.35):
            flame(d, cx + dx * s, base + cy - s * 0.2, s * 0.36, rng)


def pacifier(d, cx, cy, s, rng):
    _oval(d, cx, cy - s * 0.35, s * 0.22, s * 0.3)  # nipple, behind the shield
    _oval(d, cx, cy, s * 0.62, s * 0.3)  # shield
    d.ellipse((cx - s * 0.28, cy - s * 0.05, cx + s * 0.28, cy + s * 0.55), outline="black", width=LINE + 2)
    _oval(d, cx, cy, s * 0.13, s * 0.1, w=4)  # button


def baby(d, cx, cy, s, rng):
    r = s * 0.6
    for side in (-1, 1):
        _oval(d, cx + side * r * 0.98, cy, r * 0.17, r * 0.22)  # ears
    _oval(d, cx, cy, r, r * 0.95)
    d.arc((cx - r * 0.25, cy - r * 1.25, cx + r * 0.2, cy - r * 0.7), 200, 470, fill="black", width=LINE)  # curl
    for side in (-1, 1):  # playa goggles
        _oval(d, cx + side * r * 0.38, cy - r * 0.2, r * 0.3, r * 0.25)
        _oval(d, cx + side * r * 0.38, cy - r * 0.2, r * 0.16, r * 0.12, w=4)
    d.line((cx - r * 0.08, cy - r * 0.2, cx + r * 0.08, cy - r * 0.2), fill="black", width=LINE)
    for side in (-1, 1):
        d.line((cx + side * r * 0.68, cy - r * 0.22, cx + side * r * 0.97, cy - r * 0.3), fill="black", width=LINE)
    pacifier(d, cx, cy + r * 0.45, r * 0.55, rng)


def sun(d, cx, cy, s, rng):
    _poly(d, _star_pts(cx, cy, s * 0.8, points=12, inner=0.65, rot=rng.uniform(0, 1)))
    _oval(d, cx, cy, s * 0.42, s * 0.42)
    for side in (-1, 1):
        _oval(d, cx + side * s * 0.15, cy - s * 0.08, s * 0.05, s * 0.07, w=4)
    d.arc((cx - s * 0.2, cy - s * 0.1, cx + s * 0.2, cy + s * 0.25), 20, 160, fill="black", width=4)


def moon(d, cx, cy, s, rng):
    r = s * 0.6
    _oval(d, cx, cy, r, r)
    d.ellipse((cx - r * 0.45, cy - r * 1.05, cx + r * 1.45, cy + r * 0.85), fill="white")
    d.arc((cx - r * 0.45, cy - r * 1.05, cx + r * 1.45, cy + r * 0.85), 100, 260, fill="black", width=LINE)


def star(d, cx, cy, s, rng):
    _poly(d, _star_pts(cx, cy, s * 0.5, inner=0.5, rot=rng.uniform(-0.3, 0.3)), w=4)


HEROES = (baby, cat, the_man, rocket, planet)
SIDEKICKS = (flame, planet, pacifier, sun, moon, cat, rocket, baby)


def _free(box, taken, pad=12):
    x0, y0, x1, y1 = box
    return all(x1 + pad < a or x0 - pad > c or y1 + pad < b or y0 - pad > e for a, b, c, e in taken)


def render_coloring(seed: int | None = None) -> Image.Image:
    """One random coloring page on the badge canvas (W x H, mode "1")."""
    rng = random.Random(seed)
    img = Image.new("L", (W, H), "white")
    d = ImageDraw.Draw(img)
    hero = rng.choice(HEROES)
    sidekicks = rng.sample([f for f in SIDEKICKS if f is not hero], 3)
    caption_font = _font(TEXT_FONT, 9)
    cap_h = 40
    top, bottom = MARGIN, H - MARGIN - cap_h
    mid = (top + bottom) / 2
    taken = []
    # Hero on one side, three sidekicks stacked in the other half.
    hero_left = rng.random() < 0.5
    hx, hy, hs = W * (0.3 if hero_left else 0.7) + rng.uniform(-20, 20), mid + 10, 230
    hero(d, hx, hy, hs, rng)
    taken.append((hx - hs * 0.8, top, hx + hs * 0.8, bottom))
    sx = W * (0.75 if hero_left else 0.25)
    slots = [(sx - 140, mid - 120), (sx + 140, mid - 90), (sx, mid + 120)]
    rng.shuffle(slots)
    for f, (x, y) in zip(sidekicks, slots):
        x, y, s = x + rng.uniform(-20, 20), y + rng.uniform(-15, 15), rng.uniform(100, 125)
        edge = SAFE_INSET_PX + s * 0.85  # widest motif (planet ring) reaches 0.8 s: keep it off the label edge
        x = min(max(x, edge), W - edge)
        f(d, x, y, s, rng)
        taken.append((x - s * 0.85, y - s * 0.85, x + s * 0.85, y + s * 0.85))
    # Filler stars only in the gaps, so they never sit on top of a drawing.
    for _ in range(rng.randint(4, 7)):
        for _try in range(40):
            s = rng.uniform(55, 80)
            x, y = rng.uniform(MARGIN + s / 2, W - MARGIN - s / 2), rng.uniform(top + s / 2, bottom - s / 2)
            box = (x - s / 2, y - s / 2, x + s / 2, y + s / 2)
            if _free(box, taken):
                star(d, x, y, s, rng)
                taken.append(box)
                break
    tw = d.textlength(CAPTION, font=caption_font)
    d.text(((W - tw) / 2, H - MARGIN - cap_h + 6), CAPTION, font=caption_font, fill="black")
    return img.point(THRESHOLD, "1")


def page_files(coloring_dir: Path) -> list[Path]:
    """The pool: PNG/JPEG files in coloring_dir (none if it is missing), in a stable order."""
    if not coloring_dir.is_dir():
        return []
    return sorted(p for p in coloring_dir.iterdir() if p.suffix.lower() in PAGE_SUFFIXES and p.is_file())


def pick_page(rng: random.Random, coloring_dir: Path) -> Image.Image | None:
    """A random pool page fitted (aspect kept, white letterbox) inside SAFE_INSET_PX, mode "1"; None if no pool."""
    files = page_files(coloring_dir)
    if not files:
        return None
    with Image.open(rng.choice(files)) as src:
        rgba = src.convert("RGBA")
    flat = Image.new("RGB", rgba.size, "white")
    flat.paste(rgba, mask=rgba)  # transparent background prints as white
    page = ImageOps.contain(flat.convert("L"), (W - 2 * SAFE_INSET_PX, H - 2 * SAFE_INSET_PX))
    img = Image.new("L", (W, H), "white")
    img.paste(page, ((W - page.width) // 2, (H - page.height) // 2))
    return img.point(THRESHOLD, "1")


def coloring_page(rng: random.Random, coloring_dir: Path) -> Image.Image:
    """The bonus label: a pool page, or a drawn one when the pool is empty or the picked file is unreadable."""
    try:
        page = pick_page(rng, coloring_dir)
    except Exception:  # a corrupt file in the pool must not cost the guest the bonus
        log.exception("coloring page in %s unreadable, drawing one instead", coloring_dir)
        page = None
    return page or render_coloring(rng.getrandbits(32))
