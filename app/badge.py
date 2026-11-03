"""Badge renderer for Brother DK-1234 (60x86 mm) labels, landscape at 300 dpi."""
import random
from functools import lru_cache
from pathlib import Path

import segno
from PIL import Image, ImageDraw, ImageFont

FONT_DIR = Path(__file__).parent / "assets" / "fonts"
NAME_FONT = FONT_DIR / "Bungee-Regular.ttf"
TEXT_FONT = FONT_DIR / "Lato-Bold.ttf"

W, H = 954, 672
DPI = 300
FRAME_PX = 30  # border art lives in this outer band
MARGIN_PX = 50
NAME_MAX_PT, NAME_MIN_PT, NAME_MAX_LINES = 60, 8, 2  # 8 pt is the hard floor: shrink, never clip
# Full width, sits above the QR corner so the two can never overlap.
NAME_BOX = (MARGIN_PX, MARGIN_PX, W - MARGIN_PX, 335)

TAGLINE_MAX_PT = 20
TAGLINE_NAME_RATIO = 0.45  # tagline never outgrows 45% of the name's size
TAGLINE_MIN_PT = 8

QR_PX = 236  # ~20 mm
CAPTION_PT = 8
QR_BOX = (W - MARGIN_PX - QR_PX, H - MARGIN_PX - 36 - QR_PX, W - MARGIN_PX, H - MARGIN_PX - 36)

BORDERS = ("sun_rays", "dust_dots", "ticket_notches")


def _px(pt: float) -> int:
    return round(pt * DPI / 72)


@lru_cache(maxsize=None)
def _font(path: Path, pt: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(path), _px(pt))


def name_font(pt: int) -> ImageFont.FreeTypeFont:
    return _font(NAME_FONT, pt)


def line_height(font: ImageFont.FreeTypeFont) -> int:
    ascent, descent = font.getmetrics()
    return ascent + descent


def _chunks(word: str, font, width: int) -> list[str]:
    """Split a token wider than `width` into character runs that each fit."""
    out, cur = [], ""
    for ch in word:
        if cur and font.getlength(cur + ch) > width:
            out.append(cur)
            cur = ch
        else:
            cur += ch
    return out + [cur]


def _wrap(text: str, font, width: int) -> list[str]:
    lines, cur = [], ""
    for word in text.split():
        if font.getlength(word) > width:  # unbreakable: split by characters
            if cur:
                lines.append(cur)
            *whole, cur = _chunks(word, font, width)
            lines += whole
            continue
        trial = f"{cur} {word}".strip()
        if cur and font.getlength(trial) > width:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    return lines + [cur] if cur else lines


def _fit(text, path, lo, hi, box, max_lines):
    """Largest pt in [lo, hi] whose wrapped text fits box (binary search; falls back to lo)."""
    bw, bh = box[2] - box[0], box[3] - box[1]

    def lines_if_fits(pt):
        font = _font(path, pt)
        lines = _wrap(text, font, bw)
        ok = len(lines) <= max_lines and len(lines) * line_height(font) <= bh
        return lines if ok else None

    best = (lo, _wrap(text, _font(path, lo), bw))
    while lo <= hi:
        mid = (lo + hi) // 2
        lines = lines_if_fits(mid)
        if lines:
            best, lo = (mid, lines), mid + 1
        else:
            hi = mid - 1
    return best


def fit_name(name: str) -> tuple[int, list[str]]:
    return _fit(name, NAME_FONT, NAME_MIN_PT, NAME_MAX_PT, NAME_BOX, NAME_MAX_LINES)


def tagline_max_pt(name_pt: int) -> int:
    return max(1, min(TAGLINE_MAX_PT, int(TAGLINE_NAME_RATIO * name_pt)))


def _centered_lines(d, lines, font, box, y=None):
    lh = line_height(font)
    cx = (box[0] + box[2]) / 2
    y = box[1] + ((box[3] - box[1]) - lh * len(lines)) / 2 if y is None else y
    for line in lines:
        d.text((cx, y), line, font=font, fill=0, anchor="mt")
        y += lh


def _draw_qr(img, d, url):
    qr = segno.make(url, error="m", micro=False)
    quiet = 2
    n = qr.symbol_size(border=0)[0]
    scale = QR_PX // (n + 2 * quiet)
    side = scale * (n + 2 * quiet)
    x0, y1 = QR_BOX[2] - side, QR_BOX[3]
    y0 = y1 - side
    d.rectangle((x0, y0, x0 + side - 1, y1 - 1), fill=255)
    for r, row in enumerate(qr.matrix):
        for c, dark in enumerate(row):
            if dark:
                px, py = x0 + (c + quiet) * scale, y0 + (r + quiet) * scale
                d.rectangle((px, py, px + scale - 1, py + scale - 1), fill=0)
    cap = _font(TEXT_FONT, CAPTION_PT)
    d.text(((x0 + x0 + side) / 2, y1 + 4), "your photo", font=cap, fill=0, anchor="mt")


def _sun_rays(d, rng):
    step = 38
    for x in range(0, W, step):
        d.polygon([(x, 0), (x + step, 0), (x + step // 2, FRAME_PX - 4)], fill=0)
        d.polygon([(x, H), (x + step, H), (x + step // 2, H - FRAME_PX + 4)], fill=0)
    for y in range(0, H, step):
        d.polygon([(0, y), (0, y + step), (FRAME_PX - 4, y + step // 2)], fill=0)
        d.polygon([(W, y), (W, y + step), (W - FRAME_PX + 4, y + step // 2)], fill=0)


def _dust_dots(d, rng):
    bands = [(0, 0, W, FRAME_PX), (0, H - FRAME_PX, W, H), (0, 0, FRAME_PX, H), (W - FRAME_PX, 0, W, H)]
    for x0, y0, x1, y1 in bands:
        for _ in range((x1 - x0) * (y1 - y0) // 60):
            r = rng.choice((1, 1, 2, 2, 3, 4))
            x, y = rng.randint(x0 + r, x1 - r), rng.randint(y0 + r, y1 - r)
            d.ellipse((x - r, y - r, x + r, y + r), fill=0)


def _ticket_notches(d, rng):
    d.rectangle((0, 0, W - 1, H - 1), outline=0, width=FRAME_PX - 12)
    d.rectangle((FRAME_PX - 6, FRAME_PX - 6, W - FRAME_PX + 5, H - FRAME_PX + 5), outline=0, width=3)
    # semicircular punches on the long edges, like a torn ticket
    for x in range(60, W - 40, 80):
        for y in (0, H):
            d.ellipse((x - 14, y - 14, x + 14, y + 14), fill=255, outline=0, width=3)
    for y in (0, H):
        d.line((0, y, W, y), fill=0, width=3)


_DRAW = {"sun_rays": _sun_rays, "dust_dots": _dust_dots, "ticket_notches": _ticket_notches}


def render_badge(name: str, tagline: str, first_burn: int | None, qr_url: str | None = None,
                 border: str | None = None, rng: random.Random | None = None) -> Image.Image:
    rng = rng or random.Random()
    border = border or rng.choice(BORDERS)
    img = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(img)
    _DRAW[border](d, rng)

    # tagline + first-burn live left of the QR (full width without one)
    right = QR_BOX[0] - 20 if qr_url else W - MARGIN_PX
    gap = 15
    tag_box = (MARGIN_PX, NAME_BOX[3] + gap, right, H - MARGIN_PX - (24 if first_burn is not None else 0))
    pt, lines = fit_name(name)
    tcap = tagline_max_pt(pt)
    tpt, tlines = _fit(tagline, TEXT_FONT, min(TAGLINE_MIN_PT, tcap), tcap, tag_box, 3)
    nfont, tfont = name_font(pt), _font(TEXT_FONT, tpt)
    nh, th = line_height(nfont) * len(lines), line_height(tfont) * len(tlines)

    # centre name+tagline vertically above the first-burn line; with a QR the name stops above it
    group = nh + (gap + th if tlines else 0)
    y = MARGIN_PX + (tag_box[3] - MARGIN_PX - group) / 2
    y = max(MARGIN_PX, min(y, QR_BOX[1] - nh) if qr_url else y)
    _centered_lines(d, lines, nfont, NAME_BOX, y)
    _centered_lines(d, tlines, tfont, tag_box, y + nh + gap)
    if first_burn is not None:
        d.text(((MARGIN_PX + right) / 2, H - MARGIN_PX), f"First Burn: Baby Burn {first_burn}",
               font=_font(TEXT_FONT, 12), fill=0, anchor="ms")

    if qr_url:
        _draw_qr(img, d, qr_url)
    return img.convert("1", dither=Image.Dither.NONE)
