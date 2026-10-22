"""Synchronous image-generation service. Runs in a thread-pool worker."""
import datetime
import functools
import io
import math
import random
import re
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont
from loguru import logger

from app.core.config import settings
from app.services import languages

# BASE_DIR moves up from app/services/clock.py to the root project directory
BASE_DIR = Path(__file__).parent.parent.parent

# Display dimensions
EPD_WIDTH = 800
EPD_HEIGHT = 480

ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")

# Fonts live in fonts/<group>/*.ttf (common, hebrew, latin); the name is the file stem
FONTS_DIR = BASE_DIR / "fonts"
FONT_PATHS = {f.stem: f for f in sorted(FONTS_DIR.rglob("*.ttf"))}
VALID_FONTS = set(FONT_PATHS)

# Fallback font list if no TTF files are found
if not VALID_FONTS:
    VALID_FONTS = {"DavidLibre-Bold", "FrankRuhlLibre-Bold", "FrankRuhlLibre", "Heebo-Bold", "NotoSansHebrew-Bold"}

DEFAULT_FONT = "DavidLibre-Bold"

# What to show above the Hebrew time
VALID_CLOCK_STYLES = {"analog", "digital", "none"}
DEFAULT_CLOCK_STYLE = "analog"

# Hours (Israel time, 24h "HH:MM") between which sleep mode shows the night image
# Battery indicator, drawn in a top corner when the device reports its battery
VALID_BATTERY_DISPLAYS = {"none", "icon", "percent", "both"}
DEFAULT_BATTERY_DISPLAY = "none"
VALID_BATTERY_POSITIONS = {"left", "right"}
DEFAULT_BATTERY_POSITION = "left"

# Resting voltage of a single LiPo cell (millivolts) → charge (%), highest first
_LIPO_CURVE = [
    (4200, 100), (4150, 95), (4110, 90), (4080, 85), (4020, 80), (3980, 75),
    (3950, 70), (3910, 65), (3870, 60), (3850, 55), (3840, 50), (3820, 45),
    (3800, 40), (3790, 35), (3770, 30), (3750, 25), (3730, 20), (3710, 15),
    (3690, 10), (3610, 5), (3270, 0),
]

# Battery save mode: the device fetches this many minutes of images per connection
MIN_BATCH_MINUTES, MAX_BATCH_MINUTES = 1, 15
DEFAULT_BATCH_MINUTES = 1

DEFAULT_SLEEP_START = "22:00"
DEFAULT_SLEEP_END = "06:00"
_HHMM = re.compile(r"([01]\d|2[0-3]):([0-5]\d)")

# ── Helpers ───────────────────────────────────────────

def get_israel_time() -> datetime.datetime:
    """Calculates Israel local time with display lag offset."""
    # Naive local time; the tz database supplies the exact DST transition dates.
    local = datetime.datetime.now(ISRAEL_TZ).replace(tzinfo=None)
    return local + datetime.timedelta(seconds=settings.display_lag)


def battery_percent_from_mv(millivolts: int) -> int:
    """Estimates the charge of a LiPo cell from its voltage."""
    if millivolts >= _LIPO_CURVE[0][0]:
        return 100
    for (hi_mv, hi_pct), (lo_mv, lo_pct) in zip(_LIPO_CURVE, _LIPO_CURVE[1:]):
        if millivolts >= lo_mv:
            return round(lo_pct + (hi_pct - lo_pct) * (millivolts - lo_mv) / (hi_mv - lo_mv))
    return 0


def parse_hhmm(value: str | None) -> int | None:
    """Returns minutes after midnight for a 24h 'HH:MM' string, or None if invalid."""
    match = _HHMM.fullmatch(value.strip()) if value else None
    return int(match.group(1)) * 60 + int(match.group(2)) if match else None


def israel_time_at(unix_seconds: int) -> datetime.datetime:
    """Israel local time (naive) of a Unix timestamp, rounded down to the minute."""
    moment = datetime.datetime.fromtimestamp(unix_seconds, ISRAEL_TZ)
    return moment.replace(second=0, microsecond=0, tzinfo=None)


def in_sleep_window(start: str | None, end: str | None,
                    now: datetime.datetime | None = None) -> bool:
    """True if the current Israel time is between start and end (may span midnight).

    A missing or invalid time, or start equal to end, means the whole day.
    """
    start_min, end_min = parse_hhmm(start), parse_hhmm(end)
    if start_min is None or end_min is None or start_min == end_min:
        return True
    now = now or get_israel_time()
    now_min = now.hour * 60 + now.minute
    if start_min < end_min:
        return start_min <= now_min < end_min
    return now_min >= start_min or now_min < end_min


def get_font(size: int, font_name: str = DEFAULT_FONT) -> ImageFont.FreeTypeFont:
    """Loads specified TTF font with fallback options."""
    name = font_name if font_name in VALID_FONTS else DEFAULT_FONT
    path = FONT_PATHS.get(name)
    if path and path.exists():
        try:
            return ImageFont.truetype(str(path), size)
        except Exception as exc:
            logger.warning("Failed to load font {}: {}", path, exc)
            
    for fallback in ("DavidLibre-Bold", "NotoSansHebrew-Bold", "FrankRuhlLibre"):
        fb = FONT_PATHS.get(fallback)
        if fb and fb.exists():
            try:
                return ImageFont.truetype(str(fb), size)
            except Exception:
                pass
    return ImageFont.load_default()


# Characters a font may lack: Hebrew points (nikud), geresh/gershayim, minus, degree, colon
_OPTIONAL_CHARS = [chr(c) for c in range(0x05B0, 0x05C8)] + ["׳", "״", "-", "°", ":", "%"]
# Plain replacements for missing punctuation; missing points are simply dropped
_CHAR_FALLBACKS = {"׳": "'", "״": '"'}
# The vowel points; a font missing any of these is treated as having no nikud
_VOWEL_POINTS = frozenset(chr(c) for c in range(0x05B0, 0x05BC))
# Unpointed text needs the full spelling (ktiv male) of these words
_FULL_SPELLING = {
    "מענן": "מעונן", "בבקר": "בבוקר", "בקר": "בוקר",
    "בצהרים": "בצהריים", "הצהרים": "הצהריים", "וחמשים": "וחמישים", "חמשים": "חמישים",
    "באיר": "באייר", "בסיון": "בסיוון", "בחשון": "בחשוון",
}
_HEBREW_WORD = re.compile("[א-ת]+")
# "שתים" becomes "שתיים", except in "שתים עשרה"
_SHTAYIM = re.compile("שתים(?! עשרה)")


@functools.lru_cache(maxsize=None)
def _missing_chars(font_name: str) -> frozenset[str]:
    """Returns the optional characters that the font has no glyph for."""
    try:
        # Basic layout maps each character straight to its glyph, with no shaping
        font = ImageFont.truetype(str(FONT_PATHS[font_name]), 40,
                                  layout_engine=ImageFont.Layout.BASIC)
        notdef = font.getmask("\uffff")
        return frozenset(
            ch for ch in _OPTIONAL_CHARS
            if (mask := font.getmask(ch)).size == notdef.size and bytes(mask) == bytes(notdef)
        )
    except Exception as exc:
        logger.warning("Could not inspect font {}: {}", font_name, exc)
        return frozenset()


@functools.lru_cache(maxsize=None)
def _has_chars(font_name: str, chars: frozenset[str]) -> bool:
    """True if the font has a glyph for every one of `chars`."""
    try:
        font = ImageFont.truetype(str(FONT_PATHS[font_name]), 40,
                                  layout_engine=ImageFont.Layout.BASIC)
        notdef = font.getmask("\uffff")
        for ch in chars:
            mask = font.getmask(ch)
            if mask.size == notdef.size and bytes(mask) == bytes(notdef):
                return False
        return True
    except Exception as exc:
        logger.warning("Could not inspect font {}: {}", font_name, exc)
        return False


@functools.lru_cache(maxsize=None)
def fonts_for_language(code: str) -> list[str]:
    """Fonts that can write every text the clock draws in this language.

    Characters the server can replace or draw itself (nikud, geresh, minus,
    degree, colon, percent) are not required.
    """
    optional = set(_OPTIONAL_CHARS)
    needed = frozenset(
        ch for text in languages.all_texts(code) for ch in text
        if not ch.isspace() and ch not in optional
    )
    names = font_display_names()
    return sorted((name for name in VALID_FONTS if _has_chars(name, needed)),
                  key=lambda name: names[name].lower())


_FILE_NAME_NOISE = re.compile(r"-?(webfont|VariableFont_[\w,]+)$", re.IGNORECASE)


def _capitalise_words(text: str) -> str:
    """Capitalises the first letter of each word, keeping the rest ("Anka CLM")."""
    return " ".join(word[:1].upper() + word[1:] for word in text.split())


@functools.lru_cache(maxsize=None)
def font_display_names() -> dict[str, str]:
    """File name → name to show on the settings page.

    Uses the family name stored in the font ("Open Sans", "Anka CLM"), adding
    the style only when two fonts share a family ("Comix No2 CLM Medium").
    Falls back to a cleaned-up file name when the font has no name.
    """
    family_style = {}
    for stem, path in FONT_PATHS.items():
        try:
            family, style = ImageFont.truetype(str(path), 20).getname()
        except Exception:
            family, style = None, None
        if not family:
            family = re.sub(r"[-_]+", " ", _FILE_NAME_NOISE.sub("", stem))
        # Drop optical sizes such as "18pt" from the style ("Bold 18pt" → "Bold")
        style = " ".join(w for w in (style or "").split() if not re.fullmatch(r"\d+pt", w))
        family_style[stem] = (_capitalise_words(family), _capitalise_words(style))

    families = [family for family, _ in family_style.values()]
    names = {}
    for stem, (family, style) in family_style.items():
        shared = families.count(family) > 1
        names[stem] = f"{family} {style}".strip() if shared and style else family
    # Still the same name twice? Fall back to the file name to tell them apart
    for stem, name in names.items():
        if list(names.values()).count(name) > 1:
            names[stem] = f"{name} ({stem})"
    return names


def _adapt_text(text: str, font_name: str) -> str:
    """Drops or replaces characters the font cannot draw (e.g. nikud)."""
    missing = _missing_chars(font_name)
    if not missing:
        return text
    text = "".join(_CHAR_FALLBACKS.get(ch, "") if ch in missing else ch for ch in text)
    if missing & _VOWEL_POINTS:
        # No nikud at all: switch to the spelling used in unpointed Hebrew
        text = _HEBREW_WORD.sub(lambda m: _FULL_SPELLING.get(m.group(), m.group()), text)
        text = _SHTAYIM.sub("שתיים", text)
    return text


def _draw_temperature(draw: ImageDraw.Draw, cx: float, cy: float, temp: int,
                      font: ImageFont.FreeTypeFont, font_name: str) -> None:
    """Draws e.g. '-3°' centred on (cx, cy), drawing the minus/degree by hand if missing."""
    missing = _missing_chars(font_name)
    if "-" not in missing and "°" not in missing:
        draw.text((cx, cy), f"{temp}°", font=font, fill=0, anchor="mm")
        return

    ring_r, gap, dash_w = 5, 4, 12
    draw_dash = temp < 0 and "-" in missing
    digits = str(abs(temp)) if draw_dash else str(temp)
    if "°" not in missing:
        digits += "°"
    num_w = draw.textlength(digits, font=font)
    total_w = num_w
    if draw_dash:
        total_w += dash_w + gap
    if "°" in missing:
        total_w += gap + 2 * ring_r

    x = cx - total_w / 2
    if draw_dash:
        draw.line([(x, cy), (x + dash_w, cy)], fill=0, width=4)
        x += dash_w + gap
    draw.text((x, cy), digits, font=font, fill=0, anchor="lm")
    if "°" in missing:
        _, top, _, _ = draw.textbbox((x, cy), "0", font=font, anchor="lm")
        ring_cx = x + num_w + gap + ring_r
        draw.ellipse([ring_cx - ring_r, top, ring_cx + ring_r, top + 2 * ring_r],
                     outline=0, width=2)


# The 4 shades of gray the panel can show, darkest first
GRAY_LEVELS = (0, 85, 170, 255)
_GRAY_PALETTE = Image.new("P", (1, 1))
_GRAY_PALETTE.putpalette([v for level in GRAY_LEVELS for v in (level, level, level)] + [0, 0, 0] * 252)


def _png_bytes(img: Image.Image, gray: bool = False) -> bytes:
    """Converts the drawn image to a PNG for the panel.

    Black and white: 1 bit per pixel. Gray: the 4 shades in GRAY_LEVELS,
    2 bits per pixel, which keeps the smooth (anti-aliased) edges of text.
    """
    buf = io.BytesIO()
    if gray:
        shades = img.convert("RGB").quantize(palette=_GRAY_PALETTE, dither=Image.Dither.NONE)
        shades.save(buf, format="PNG", optimize=True, bits=2)
    else:
        img.convert("1", dither=Image.Dither.NONE).save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf.read()


# ── Drawing ───────────────────────────────────────────

def _draw_weather_icon(draw: ImageDraw.Draw, cx: int, cy: int,
                       icon_key: str, size: int = 38) -> None:
    """Draws vector weather icons on the canvas."""
    s = size

    def cloud(ox: int = 0, oy: int = 0, scale: float = 1.0) -> None:
        # Puffy cloud: overlapping circles (x, y, radius in units of the icon size).
        # Drawing them all in black, then slightly smaller in white, leaves only
        # the outline of their union.
        puffs = [
            (-0.56, 0.10, 0.19), (-0.30, -0.10, 0.24), (0.05, -0.22, 0.28),
            (0.36, -0.06, 0.22), (0.58, 0.12, 0.18),
            (-0.34, 0.26, 0.19), (-0.04, 0.30, 0.20), (0.28, 0.27, 0.19),
        ]
        unit = s * scale
        stroke = 2.5
        for grow, fill in ((stroke, 0), (0, 255)):
            for px, py, pr in puffs:
                x, y, r = cx + ox + px * unit, cy + oy + py * unit, pr * unit + grow
                draw.ellipse([x - r, y - r, x + r, y + r], fill=fill)
        # Fill the middle, which the circles do not fully cover
        mx, my = cx + ox, cy + oy + 0.06 * unit
        draw.ellipse([mx - 0.5 * unit, my - 0.2 * unit, mx + 0.5 * unit, my + 0.2 * unit], fill=255)

    if icon_key == "sun":
        r = s // 2
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=255, outline=0, width=3)
        for a in range(0, 360, 45):
            rad = math.radians(a)
            draw.line([cx + (r + 4)  * math.cos(rad), cy + (r + 4)  * math.sin(rad),
                       cx + (r + 13) * math.cos(rad), cy + (r + 13) * math.sin(rad)],
                      fill=0, width=3)
    elif icon_key == "sun_cloud":
        sr = s // 3
        scx, scy = cx - s // 3, cy - s // 4
        draw.ellipse([scx - sr, scy - sr, scx + sr, scy + sr], fill=255, outline=0, width=2)
        for a in range(0, 360, 60):
            rad = math.radians(a)
            draw.line([scx + (sr + 3) * math.cos(rad), scy + (sr + 3) * math.sin(rad),
                       scx + (sr + 9) * math.cos(rad), scy + (sr + 9) * math.sin(rad)],
                      fill=0, width=2)
        cloud(s // 5, s // 5, 0.85)
    elif icon_key == "cloud":
        cloud()
    elif icon_key == "cloud_rain":
        cloud(0, -s // 5, 0.9)
        for ox in (-s // 3, -s // 8, s // 8, s // 3):
            draw.line([cx + ox, cy + s // 4, cx + ox - 4, cy + s // 2 + 4], fill=0, width=2)
    elif icon_key == "cloud_snow":
        cloud(0, -s // 5, 0.9)
        for ox in (-s // 3, -s // 8, s // 8, s // 3):
            x, y = cx + ox, cy + s // 2 + 2
            for a in (0, 60, 120):
                rad = math.radians(a)
                draw.line([x - 6 * math.cos(rad), y - 6 * math.sin(rad),
                           x + 6 * math.cos(rad), y + 6 * math.sin(rad)],
                          fill=0, width=2)
    elif icon_key == "thunder":
        cloud(0, -s // 4, 0.9)
        pts = [(cx + 4, cy + s // 6), (cx - 6, cy + s // 2),
               (cx + 2, cy + s // 2), (cx - 8, cy + s)]
        draw.line(pts, fill=0, width=3)


def _draw_analog_clock(draw: ImageDraw.Draw, cx: int, cy: int, r: int,
                       h24: int, m: int, font_name: str) -> None:
    """Draws small analog clock face at the top center."""
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=0, width=3)
    for i in range(12):
        angle = math.radians(i * 30 - 90)
        if i % 3 == 0:
            draw.line([cx + (r - 4)  * math.cos(angle), cy + (r - 4)  * math.sin(angle),
                       cx + (r - 12) * math.cos(angle), cy + (r - 12) * math.sin(angle)],
                      fill=0, width=3)
        else:
            draw.line([cx + (r - 4) * math.cos(angle), cy + (r - 4) * math.sin(angle),
                       cx + (r - 9) * math.cos(angle), cy + (r - 9) * math.sin(angle)],
                      fill=0, width=2)
    num_font = get_font(max(12, r // 4), font_name)
    for num, deg in ((12, -90), (3, 0), (6, 90), (9, 180)):
        angle = math.radians(deg)
        draw.text((cx + (r - 18) * math.cos(angle), cy + (r - 18) * math.sin(angle)),
                  str(num), font=num_font, fill=0, anchor="mm")
    h12 = h24 % 12
    hour_angle = math.radians((h12 + m / 60) * 30 - 90)
    draw.line([cx, cy, cx + (r * 0.55) * math.cos(hour_angle),
               cy + (r * 0.55) * math.sin(hour_angle)], fill=0, width=4)
    min_angle = math.radians(m * 6 - 90)
    draw.line([cx, cy, cx + (r * 0.75) * math.cos(min_angle),
               cy + (r * 0.75) * math.sin(min_angle)], fill=0, width=2)
    draw.ellipse([cx - 4, cy - 4, cx + 4, cy + 4], fill=0)

def _ink_bbox(draw: ImageDraw.Draw, text: str,
              font: ImageFont.FreeTypeFont) -> tuple[float, float, float, float]:
    """Bounding box of the drawn pixels, relative to the left end of the baseline."""
    return draw.textbbox((0, 0), text, font=font, anchor="ls")


def _layout_lines(draw: ImageDraw.Draw, lines: list[tuple[str, int]],
                  box: tuple[int, int, int, int], font_name: str,
                  gap: int = 6, min_size: int = 14) -> list[tuple[str, ImageFont.FreeTypeFont, float, float]]:
    """Fits stacked lines of (text, preferred size) inside box = (x0, y0, x1, y1).

    Sizes are reduced until every line fits the width and the stack fits the
    height, measured on the real drawn pixels (tall letters and nikud included).
    Returns (text, font, centre x, centre y) for each line.
    """
    x0, y0, x1, y1 = box
    scale = 1.0
    while True:
        fitted = []
        for text, preferred in lines:
            size = max(min_size, int(preferred * scale))
            font = get_font(size, font_name)
            l, t, r, b = _ink_bbox(draw, text, font)
            while (r - l) > (x1 - x0) and size > min_size:
                size = max(min_size, size - 2)
                font = get_font(size, font_name)
                l, t, r, b = _ink_bbox(draw, text, font)
            fitted.append((text, font, b - t, size))
        total = sum(h for _, _, h, _ in fitted) + gap * (len(fitted) - 1)
        if total <= (y1 - y0) or all(size <= min_size for *_, size in fitted):
            break
        scale *= 0.94

    placed = []
    y = y0 + (y1 - y0 - total) / 2
    for text, font, h, _ in fitted:
        placed.append((text, font, (x0 + x1) / 2, y + h / 2))
        y += h + gap
    return placed


def _draw_centered(draw: ImageDraw.Draw, text: str, font: ImageFont.FreeTypeFont,
                   cx: float, cy: float, fill: int = 0) -> None:
    """Draws text so that its drawn pixels are centred on (cx, cy)."""
    l, t, r, b = _ink_bbox(draw, text, font)
    draw.text((cx - (l + r) / 2, cy - (t + b) / 2), text, font=font, fill=fill, anchor="ls")


def _middle_anchor_y(draw: ImageDraw.Draw, text: str, font: ImageFont.FreeTypeFont,
                     ink_cy: float) -> float:
    """Returns the y to use with a middle ('m') anchor so the ink is centred on ink_cy."""
    _, t, _, b = draw.textbbox((0, 0), text, font=font, anchor="lm")
    return ink_cy - (t + b) / 2


def _draw_digital_clock(draw: ImageDraw.Draw, box: tuple[int, int, int, int],
                        h24: int, m: int, font_name: str) -> None:
    """Draws HH:mm inside box, in place of the analog clock face."""
    hh, mm = f"{h24:02d}", f"{m:02d}"
    if ":" not in _missing_chars(font_name):
        text, font, cx, cy = _layout_lines(draw, [(f"{hh}:{mm}", 100)], box, font_name)[0]
        _draw_centered(draw, text, font, cx, cy)
        return
    # Font has no colon: draw the two dots by hand between the numbers
    gap, dot_r = 16, 6
    x0, y0, x1, y1 = box
    text, font, cx, cy = _layout_lines(
        draw, [(hh + mm, 100)], (x0 + gap, y0, x1 - gap, y1), font_name)[0]
    cy_m = _middle_anchor_y(draw, text, font, cy)
    draw.text((cx - gap, cy_m), hh, font=font, fill=0, anchor="rm")
    draw.text((cx + gap, cy_m), mm, font=font, fill=0, anchor="lm")
    dot_dy = max(8, font.size * 0.18)
    for dy in (-dot_dy, dot_dy):
        draw.ellipse([cx - dot_r, cy + dy - dot_r, cx + dot_r, cy + dy + dot_r], fill=0)

def _draw_battery(draw: ImageDraw.Draw, percent: int, charging: bool, display: str,
                  position: str, font_name: str, x_left: int, x_right: int, cy: int) -> None:
    """Draws the battery icon and/or percentage in a top corner of the frame."""
    body_w, body_h, nub_w, gap = 30, 15, 3, 6
    plus = 9  # size of the hand-drawn '+' shown while charging

    text = f"{percent}%" if display in ("percent", "both") else ""
    # The percent sign is missing from some fonts; fall back to the default one
    text_font = get_font(22, DEFAULT_FONT if "%" in _missing_chars(font_name) else font_name)
    text_w = draw.textlength(text, font=text_font) if text else 0
    show_icon = display in ("icon", "both")

    total_w = sum(w + gap for w in (
        body_w + nub_w if show_icon else 0, text_w, plus if charging else 0) if w) - gap
    x = x_left if position == "left" else x_right - total_w

    if show_icon:
        top = cy - body_h / 2
        draw.rectangle([x, top, x + body_w, top + body_h], outline=0, width=2)
        draw.rectangle([x + body_w, cy - 4, x + body_w + nub_w, cy + 4], fill=0)
        fill_w = round((body_w - 7) * max(0, min(100, percent)) / 100)
        if fill_w > 0:
            draw.rectangle([x + 4, top + 4, x + 3 + fill_w, top + body_h - 4], fill=0)
        x += body_w + nub_w + gap
    if text:
        draw.text((x, _middle_anchor_y(draw, text, text_font, cy)), text,
                  font=text_font, fill=0, anchor="lm")
        x += text_w + gap
    if charging:
        mid = x + plus / 2
        draw.line([(x, cy), (x + plus, cy)], fill=0, width=3)
        draw.line([(mid, cy - plus / 2), (mid, cy + plus / 2)], fill=0, width=3)


# ── Image generators ──────────────────────────────────

def _generate_night_image(font_name: str, language: str = languages.DEFAULT_LANGUAGE,
                          gray: bool = False) -> bytes:
    """Generates night screen layout when sleep_time is True."""
    W, H = EPD_WIDTH, EPD_HEIGHT
    img = Image.new("L", (W, H), color=0)
    draw = ImageDraw.Draw(img)

    rng = random.Random(42)
    for _ in range(60):
        x = rng.randint(20, W - 20)
        y = rng.randint(20, H - 20)
        size = rng.choice([1, 1, 2, 2, 3])
        draw.ellipse([x - size, y - size, x + size, y + size], fill=255)

    mx, my, mr = 100, 90, 55
    draw.ellipse([mx - mr, my - mr, mx + mr, my + mr], fill=255)
    draw.ellipse([mx - mr + 16, my - mr - 12, mx + mr + 16, my - mr - 12 + mr * 2], fill=0)

    sleeping_path = settings.font_dir / "sleeping.png"
    if sleeping_path.exists():
        try:
            sleeping = Image.open(sleeping_path).convert("L")
            mask = sleeping.point(lambda p: 255 if p < 128 else 0)
            white_lines = Image.new("L", sleeping.size, 255)
            black_bg   = Image.new("L", sleeping.size, 0)
            result = Image.composite(white_lines, black_bg, mask)
            sw, sh = 380, 280
            result = result.resize((sw, sh), Image.LANCZOS)
            mask_r = mask.resize((sw, sh), Image.LANCZOS)
            img.paste(result, (W - sw - 20, (H - sh) // 2), mask=mask_r)
        except Exception as exc:
            logger.warning("sleeping image error: {}", exc)

    # Title and subtitle, fitted into the space left of the picture
    lang = languages.get(language)
    title, subtitle = (_adapt_text(languages.styled(lang, t), font_name) for t in lang.night_texts())
    box = (20, H // 2 - 110, W - 380 - 40, H // 2 + 110)
    placed = _layout_lines(draw, [(title, 72), (subtitle, 44)], box, font_name, gap=40)
    for (text, font, cx, cy), fill in zip(placed, (255, 180)):
        _draw_centered(draw, text, font, cx, cy, fill=fill)
    return _png_bytes(img, gray)


def generate_clock_image(
    font_name:   str        = DEFAULT_FONT,
    sleep_time:  bool       = False,
    weather:     dict | None = None,
    jewish_date: str | None  = None,
    clock_style: str        = DEFAULT_CLOCK_STYLE,
    language:    str        = languages.DEFAULT_LANGUAGE,
    jewish_parts: tuple[int, str, int] | None = None,
    battery:     int | None  = None,
    charging:    bool       = False,
    battery_display:  str   = DEFAULT_BATTERY_DISPLAY,
    battery_position: str   = DEFAULT_BATTERY_POSITION,
    now: datetime.datetime | None = None,
    gray: bool = False,
) -> bytes:
    """Generates the clock screen image (gray: 4 shades instead of black and white).

    jewish_parts is (day, month name, year) from hebcal; it is written in the
    clock's language. jewish_date is an already formatted 'date\nyear' string.
    """
    fn = font_name if font_name in VALID_FONTS else DEFAULT_FONT
    lang = languages.get(language)

    if sleep_time:
        return _generate_night_image(fn, language, gray)

    now  = now or get_israel_time()
    h24, m = now.hour, now.minute

    W, H = EPD_WIDTH, EPD_HEIGHT
    img  = Image.new("L", (W, H), color=255)
    draw = ImageDraw.Draw(img)

    PAD1, PAD2 = 8, 16
    draw.rectangle([PAD1, PAD1, W - PAD1, H - PAD1], outline=0, width=3)
    draw.rectangle([PAD2, PAD2, W - PAD2, H - PAD2], outline=0, width=1)

    def text(value: str) -> str:
        """A text as drawn: the language's capitalisation, then the font's fallbacks."""
        return _adapt_text(languages.styled(lang, value), fn)

    lines, period = lang.time_text(h24, m)
    time_lines  = [text(l) for l in lines]
    period_line = text(period)

    sep_y     = H - 105
    bar_cy    = H - 52
    bar_left  = PAD2 + 8
    bar_right = W - PAD2 - 8
    bar_width = bar_right - bar_left
    div_x     = bar_left + bar_width // 3
    div_x2    = bar_left + 2 * bar_width // 3
    draw.line([(bar_left, sep_y), (bar_right, sep_y)], fill=0, width=1)
    draw.line([(div_x,  H - 92), (div_x,  H - 15)], fill=0, width=1)
    draw.line([(div_x2, H - 92), (div_x2, H - 15)], fill=0, width=1)

    def draw_block(lines: list[tuple[str, int]], box: tuple[int, int, int, int],
                   gap: int = 6) -> None:
        for text, font, cx, cy in _layout_lines(draw, lines, box, fn, gap=gap):
            _draw_centered(draw, text, font, cx, cy)

    # ── Battery (only when the device reported it) ──
    show_battery = battery is not None and battery_display in ("icon", "percent", "both")
    if show_battery:
        _draw_battery(draw, battery, charging, battery_display, battery_position, fn,
                      PAD2 + 12, W - PAD2 - 12, PAD2 + 20)

    # ── Clock and Hebrew time ──
    text_left, text_right = 30, W - 30
    text_bottom = sep_y - 8
    if clock_style == "none":
        # No clock: larger text, centred in the whole area above the bottom bar
        # (starting below the battery indicator when there is one)
        text_top = PAD2 + (36 if show_battery else 10)
        draw_block([(l, 120) for l in time_lines],
                   (text_left, text_top, text_right, text_bottom), gap=16)
    else:
        clock_cx, clock_cy, clock_r = W // 2, PAD2 + 75, 68
        if clock_style == "digital":
            _draw_digital_clock(draw, (60, PAD2 + 8, W - 60, clock_cy + clock_r), h24, m, fn)
        else:
            _draw_analog_clock(draw, clock_cx, clock_cy, clock_r, h24, m, fn)
        draw_block([(l, 100) for l in time_lines],
                   (text_left, clock_cy + clock_r + 8, text_right, text_bottom), gap=12)

    # ── Bottom bar ──
    cell_top, cell_bottom = sep_y + 5, H - PAD2 - 5

    day_name  = text(lang.day_name(now.weekday()))
    if jewish_parts:
        jewish_date = "\n".join(text(part) for part in lang.jewish_date(*jewish_parts))
    if jewish_date:
        jewish_date = _adapt_text(jewish_date, fn)
    if jewish_date and "\n" in jewish_date:
        date_str, year_str = jewish_date.split("\n", 1)
        date_lines = [(day_name, 28), (date_str, 26), (year_str, 22)]
    else:
        date_str = jewish_date if jewish_date else text(lang.date_text(now.day, now.month))
        date_lines = [(day_name, 34), (date_str, 34)]
    draw_block(date_lines, (bar_left + 5, cell_top, div_x - 5, cell_bottom), gap=5)

    if period_line:
        draw_block([(period_line, 34)], (div_x + 5, cell_top, div_x2 - 5, cell_bottom))

    if weather:
        icon_x = div_x2 + (bar_right - div_x2) // 4
        _draw_weather_icon(draw, icon_x, bar_cy, weather.get("icon_key", "cloud"), size=34)

        # Temperature and description share the right half of the cell, clear of the icon
        temp = weather["temp"]
        desc = text(lang.weather_text(weather.get("icon_key", "cloud")))
        text_box = ((div_x2 + bar_right) // 2 + 4, cell_top, bar_right - 4, cell_bottom)
        (temp_str, temp_font, cx, temp_cy), (_, desc_font, _, desc_cy) = _layout_lines(
            draw, [(str(temp), 40), (desc, 34)], text_box, fn, gap=5)
        _draw_temperature(draw, cx, _middle_anchor_y(draw, temp_str, temp_font, temp_cy),
                          temp, temp_font, fn)
        _draw_centered(draw, desc, desc_font, cx, desc_cy)

    return _png_bytes(img, gray)


def generate_blank_image() -> bytes:
    """Generates a blank white PNG image for screen power-saving / blank mode."""
    img = Image.new("1", (EPD_WIDTH, EPD_HEIGHT), 255)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def log_available_fonts() -> None:
    """Logs list of available system TTF fonts."""
    if FONT_PATHS:
        logger.info("available fonts: {}", ", ".join(sorted(FONT_PATHS, key=str.lower)))
    else:
        logger.warning("no font files found in {}", FONTS_DIR)