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

# BASE_DIR moves up from app/services/clock.py to the root project directory
BASE_DIR = Path(__file__).parent.parent.parent

# Display dimensions
EPD_WIDTH = 800
EPD_HEIGHT = 480

ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")

# Dynamic font loading from root directory
FONTS_DIR = BASE_DIR / "fonts"
VALID_FONTS = {f.stem for f in FONTS_DIR.glob("*.ttf")}

# Fallback font list if no TTF files are found
if not VALID_FONTS:
    VALID_FONTS = {"DavidLibre-Bold", "FrankRuhlLibre-Bold", "FrankRuhlLibre", "Heebo-Bold", "NotoSansHebrew-Bold"}

DEFAULT_FONT = "DavidLibre-Bold"

# What to show above the Hebrew time
VALID_CLOCK_STYLES = {"analog", "digital", "none"}
DEFAULT_CLOCK_STYLE = "analog"

# ── Hebrew time tables ────────────────────────────────

HOURS = [
    "אַחַת", "שְׁתַּיִם", "שָׁלוֹשׁ", "אַרְבַּע", "חָמֵשׁ", "שֵׁשׁ",
    "שֶׁבַע", "שְׁמוֹנֶה", "תֵּשַׁע", "עֶשֶׂר", "אַחַת עֶשְׂרֵה", "שְׁתֵּים עֶשְׂרֵה"
]
MINUTE_PREFIX = [
    "", "וְדַקָּה אַחַת", "וּשְׁתֵּי דַקּוֹת", "וְשָׁלוֹשׁ דַקּוֹת",
    "וְאַרְבַּע דַקּוֹת", "וְחָמֵשׁ דַקּוֹת", "וְשֵׁשׁ דַקּוֹת", "וְשֶׁבַע דַקּוֹת",
    "וּשְׁמוֹנֶה דַקּוֹת", "וְתֵשַׁע דַקּוֹת", "וְעֶשֶׂר דַקּוֹת",
    "וְאַחַת עֶשְׂרֵה דַּקּוֹת", "וּשְׁתֵּים עֶשְׂרֵה דַּקּוֹת",
    "וּשְׁלוֹשׁ עֶשְׂרֵה דַּקּוֹת", "וְאַרְבַּע עֶשְׂרֵה דַּקּוֹת",
    "וָרֶבַע", "וְשֵׁשׁ עֶשְׂרֵה דַּקּוֹת", "וּשְׁבַע עֶשְׂרֵה דַּקּוֹת",
    "וּשְׁמוֹנֶה עֶשְׂרֵה דַּקּוֹת", "וּתְשַׁע עֶשְׂרֵה דַּקּוֹת",
    "וְעֶשְׂרִים דַקּוֹת", "וְעֶשְׂרִים וְאַחַת", "וְעֶשְׂרִים וּשְׁתַּיִם",
    "וְעֶשְׂרִים וְשָׁלוֹשׁ", "וְעֶשְׂרִים וְאַרְבַּע", "וְעֶשְׂרִים וְחָמֵשׁ",
    "וְעֶשְׂרִים וְשֵׁשׁ", "וְעֶשְׂרִים וְשֶׁבַע", "וְעֶשְׂרִים וּשְׁמוֹנֶה",
    "וְעֶשְׂרִים וְתֵשַׁע", "וּשְׁלוֹשִׁים", "וּשְׁלוֹשִׁים וְאַחַת",
    "וּשְׁלוֹשִׁים וּשְׁתַּיִם", "וּשְׁלוֹשִׁים וְשָׁלוֹשׁ", "וּשְׁלוֹשִׁים וְאַרְבַּע",
    "וּשְׁלוֹשִׁים וְחָמֵשׁ", "וּשְׁלוֹשִׁים וְשֵׁשׁ", "וּשְׁלוֹשִׁים וְשֶׁבַע",
    "וּשְׁלוֹשִׁים וּשְׁמוֹנֶה", "וּשְׁלוֹשִׁים וְתֵשַׁע", "וְאַרְבָּעִים",
    "וְאַרְבָּעִים וְאַחַת", "וְאַרְבָּעִים וּשְׁתַּיִם", "וְאַרְבָּעִים וְשָׁלוֹשׁ",
    "וְאַרְבָּעִים וְאַרְבַּע", "וְאַרְבָּעִים וְחָמֵשׁ", "וְאַרְבָּעִים וְשֵׁשׁ",
    "וְאַרְבָּעִים וְשֶׁבַע", "וְאַרְבָּעִים וּשְׁמוֹנֶה", "וְאַרְבָּעִים וְתֵשַׁע",
    "וַחֲמִשִּׁים", "וַחֲמִשִּׁים וְאַחַת", "וַחֲמִשִּׁים וּשְׁתַּיִם",
    "וַחֲמִשִּׁים וְשָׁלוֹשׁ", "וַחֲמִשִּׁים וְאַרְבַּע", "וַחֲמִשִּׁים וְחָמֵשׁ",
    "וַחֲמִשִּׁים וְשֵׁשׁ", "וַחֲמִשִּׁים וְשֶׁבַע", "וַחֲמִשִּׁים וּשְׁמוֹנֶה",
    "וַחֲמִשִּׁים וְתֵשַׁע",
]

PERIOD_WORDS = {
    "בַּבֹּקֶר", "בַּצָּהֳרַיִם", "אַחַר הַצָּהֳרַיִם",
    "בָּעֶרֶב", "בַּלַּיְלָה", "לִפְנוֹת בֹּקֶר",
}

MONTHS_HE = [
    "בְּיָנוּאָר", "בְּפֶבְּרוּאָר", "בְּמָרְץ", "בְּאַפְּרִיל",
    "בְּמַאי", "בְּיוּנִי", "בְּיוּלִי", "בְּאוֹגוּסְט",
    "בְּסֶפְּטֶמְבֶּר", "בְּאוֹקְטוֹבֶּר", "בְּנוֹבֶמְבֶּר", "בְּדֶצֶמְבֶּר",
]
DAYS_HE = [
    "יוֹם שֵׁנִי", "יוֹם שְׁלִישִׁי", "יוֹם רְבִיעִי",
    "יוֹם חֲמִישִׁי", "יוֹם שִׁישִּׁי", "שַׁבָּת", "יוֹם רִאשׁוֹן",
]

# ── Helpers ───────────────────────────────────────────

def get_israel_time() -> datetime.datetime:
    """Calculates Israel local time with display lag offset."""
    # Naive local time; the tz database supplies the exact DST transition dates.
    local = datetime.datetime.now(ISRAEL_TZ).replace(tzinfo=None)
    return local + datetime.timedelta(seconds=settings.display_lag)


def get_font(size: int, font_name: str = DEFAULT_FONT) -> ImageFont.FreeTypeFont:
    """Loads specified TTF font with fallback options."""
    name = font_name if font_name in VALID_FONTS else DEFAULT_FONT
    path = FONTS_DIR / f"{name}.ttf"
    if path.exists():
        try:
            return ImageFont.truetype(str(path), size)
        except Exception as exc:
            logger.warning("Failed to load font {}: {}", path, exc)
            
    for fallback in ("DavidLibre-Bold", "NotoSansHebrew-Bold", "FrankRuhlLibre"):
        fb = FONTS_DIR / f"{fallback}.ttf"
        if fb.exists():
            try:
                return ImageFont.truetype(str(fb), size)
            except Exception:
                pass
    return ImageFont.load_default()


# Characters a font may lack: Hebrew points (nikud), geresh/gershayim, minus, degree, colon
_OPTIONAL_CHARS = [chr(c) for c in range(0x05B0, 0x05C8)] + ["׳", "״", "-", "°", ":"]
# Plain replacements for missing punctuation; missing points are simply dropped
_CHAR_FALLBACKS = {"׳": "'", "״": '"'}
# The vowel points; a font missing any of these is treated as having no nikud
_VOWEL_POINTS = frozenset(chr(c) for c in range(0x05B0, 0x05BC))
# Unpointed text needs the full spelling (ktiv male) of these words
_FULL_SPELLING = {
    "מענן": "מעונן", "בבקר": "בבוקר", "בקר": "בוקר",
    "בצהרים": "בצהריים", "הצהרים": "הצהריים", "וחמשים": "וחמישים",
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
        font = ImageFont.truetype(str(FONTS_DIR / f"{font_name}.ttf"), 40,
                                  layout_engine=ImageFont.Layout.BASIC)
        notdef = font.getmask("\uffff")
        return frozenset(
            ch for ch in _OPTIONAL_CHARS
            if (mask := font.getmask(ch)).size == notdef.size and bytes(mask) == bytes(notdef)
        )
    except Exception as exc:
        logger.warning("Could not inspect font {}: {}", font_name, exc)
        return frozenset()


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


def _png_bytes(img: Image.Image) -> bytes:
    """Converts PIL Image to 1-bit PNG byte buffer."""
    buf = io.BytesIO()
    img.convert("1", dither=Image.Dither.NONE).save(buf, format="PNG", optimize=True)
    buf.seek(0)
    return buf.read()


def _get_time_period(h: int) -> str:
    """Returns Hebrew period phrase for given hour."""
    if 6  <= h < 12: return "בַּבֹּקֶר"
    if 12 <= h < 16: return "בַּצָּהֳרַיִם"
    if 16 <= h < 18: return "אַחַר הַצָּהֳרַיִם"
    if 18 <= h < 21: return "בָּעֶרֶב"
    if 21 <= h < 24: return "בַּלַּיְלָה"
    if 0  <= h < 3:  return "בַּלַּיְלָה"
    return "לִפְנוֹת בֹּקֶר"


def _get_time_lines(h24: int, m: int) -> list[str]:
    """Generates time strings in Hebrew words."""
    h12 = h24 % 12 or 12
    period = _get_time_period(h24)
    mp = MINUTE_PREFIX[m]
    hp = HOURS[h12 - 1]
    if len(hp + mp) > 25:
        return [hp, mp, period]
    return [hp + " " + mp, period]

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
                   cx: float, cy: float) -> None:
    """Draws text so that its drawn pixels are centred on (cx, cy)."""
    l, t, r, b = _ink_bbox(draw, text, font)
    draw.text((cx - (l + r) / 2, cy - (t + b) / 2), text, font=font, fill=0, anchor="ls")


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

# ── Image generators ──────────────────────────────────

def _generate_night_image(font_name: str) -> bytes:
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

    text_cx = (W - 380 - 40) // 2
    draw.text((text_cx, H // 2 - 30), _adapt_text("זְמַן לִישׁוֹן", font_name),
              font=get_font(72, font_name), fill=255, anchor="mm")
    draw.text((text_cx, H // 2 + 55), _adapt_text("לַיְלָה טוֹב", font_name),
              font=get_font(44, font_name), fill=180, anchor="mm")
    return _png_bytes(img)


def generate_clock_image(
    font_name:   str        = DEFAULT_FONT,
    sleep_time:  bool       = False,
    weather:     dict | None = None,
    jewish_date: str | None  = None,
    clock_style: str        = DEFAULT_CLOCK_STYLE,
) -> bytes:
    """Generates full Hebrew clock screen image."""
    fn = font_name if font_name in VALID_FONTS else DEFAULT_FONT

    if sleep_time:
        return _generate_night_image(fn)

    now  = get_israel_time()
    h24, m = now.hour, now.minute

    W, H = EPD_WIDTH, EPD_HEIGHT
    img  = Image.new("L", (W, H), color=255)
    draw = ImageDraw.Draw(img)

    PAD1, PAD2 = 8, 16
    draw.rectangle([PAD1, PAD1, W - PAD1, H - PAD1], outline=0, width=3)
    draw.rectangle([PAD2, PAD2, W - PAD2, H - PAD2], outline=0, width=1)

    lines       = _get_time_lines(h24, m)
    time_lines  = [_adapt_text(l, fn) for l in lines if l not in PERIOD_WORDS]
    period_line = _adapt_text(next((l for l in lines if l in PERIOD_WORDS), ""), fn)

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

    # ── Clock and Hebrew time ──
    text_left, text_right = 30, W - 30
    text_bottom = sep_y - 8
    if clock_style == "none":
        # No clock: larger text, centred in the whole area above the bottom bar
        draw_block([(l, 120) for l in time_lines],
                   (text_left, PAD2 + 10, text_right, text_bottom), gap=16)
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

    day_name  = _adapt_text(DAYS_HE[now.weekday()], fn)
    if jewish_date:
        jewish_date = _adapt_text(jewish_date, fn)
    if jewish_date and "\n" in jewish_date:
        date_str, year_str = jewish_date.split("\n", 1)
        date_lines = [(day_name, 28), (date_str, 26), (year_str, 22)]
    else:
        date_str = jewish_date if jewish_date else _adapt_text(f"{now.day} {MONTHS_HE[now.month - 1]}", fn)
        date_lines = [(day_name, 34), (date_str, 34)]
    draw_block(date_lines, (bar_left + 5, cell_top, div_x - 5, cell_bottom), gap=5)

    if period_line:
        draw_block([(period_line, 34)], (div_x + 5, cell_top, div_x2 - 5, cell_bottom))

    if weather:
        icon_x = div_x2 + (bar_right - div_x2) // 4
        _draw_weather_icon(draw, icon_x, bar_cy, weather.get("icon_key", "cloud"), size=34)

        # Temperature and description share the right half of the cell, clear of the icon
        temp = weather["temp"]
        desc = _adapt_text(weather.get("desc", ""), fn)
        text_box = ((div_x2 + bar_right) // 2 + 4, cell_top, bar_right - 4, cell_bottom)
        (temp_str, temp_font, cx, temp_cy), (_, desc_font, _, desc_cy) = _layout_lines(
            draw, [(str(temp), 40), (desc, 34)], text_box, fn, gap=5)
        _draw_temperature(draw, cx, _middle_anchor_y(draw, temp_str, temp_font, temp_cy),
                          temp, temp_font, fn)
        _draw_centered(draw, desc, desc_font, cx, desc_cy)

    return _png_bytes(img)


def generate_blank_image() -> bytes:
    """Generates a blank white PNG image for screen power-saving / blank mode."""
    img = Image.new("1", (EPD_WIDTH, EPD_HEIGHT), 255)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def log_available_fonts() -> None:
    """Logs list of available system TTF fonts."""
    found = [f for f in VALID_FONTS if (FONTS_DIR / f"{f}.ttf").exists()]
    if found:
        logger.info("available fonts: {}", ", ".join(sorted(found)))
    else:
        logger.warning("no Hebrew font files found in {}", FONTS_DIR)