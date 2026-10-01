"""Synchronous image-generation service. Runs in a thread-pool worker."""
import datetime
import functools
import io
import math
import random
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
    return "".join(_CHAR_FALLBACKS.get(ch, "") if ch in missing else ch for ch in text)


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
        w, h = int(s * 1.4 * scale), int(s * 0.7 * scale)
        pts = []
        for a in range(180, 361, 8):
            pts.append((ox + cx + int(w / 2 * math.cos(math.radians(a))),
                        oy + cy + int(h / 2 * math.sin(math.radians(a)))))
        for centre, rx, dy in [
            (int(w * 0.25),  int(h * 0.6  * scale), int(h * 0.2)),
            (0,              int(h * 0.75 * scale), int(h * 0.3)),
            (-int(w * 0.25), int(h * 0.55 * scale), int(h * 0.1)),
        ]:
            for a in range(0, 181, 8):
                pts.append((ox + cx + centre + int(rx * math.cos(math.radians(a))),
                            oy + cy - dy     + int(rx * math.sin(math.radians(a)))))
        draw.polygon(pts, fill=255, outline=0)

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
            x, y = cx + ox, cy + s // 3
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

def _draw_digital_clock(draw: ImageDraw.Draw, cx: int, cy: int,
                        h24: int, m: int, font_name: str) -> None:
    """Draws HH:mm at the top center, in place of the analog clock face."""
    font = get_font(100, font_name)
    hh, mm = f"{h24:02d}", f"{m:02d}"
    if ":" not in _missing_chars(font_name):
        draw.text((cx, cy), f"{hh}:{mm}", font=font, fill=0, anchor="mm")
        return
    # Font has no colon: draw the two dots by hand between the numbers
    gap, dot_r = 16, 6
    draw.text((cx - gap, cy), hh, font=font, fill=0, anchor="rm")
    draw.text((cx + gap, cy), mm, font=font, fill=0, anchor="lm")
    for dy in (-18, 18):
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

    font_small  = get_font(34,  fn)
    n           = len(time_lines)

    if clock_style == "none":
        # No clock: larger text, centred in the whole area above the bottom bar
        font_large = get_font(120, fn)
        line_h     = 115
        ty         = (PAD2 + H - 105) // 2 - (n - 1) * line_h // 2
    else:
        font_large = get_font(100, fn)
        clock_cx, clock_cy, clock_r = W // 2, PAD2 + 75, 68
        if clock_style == "digital":
            _draw_digital_clock(draw, clock_cx, clock_cy, h24, m, fn)
        else:
            _draw_analog_clock(draw, clock_cx, clock_cy, clock_r, h24, m, fn)

        text_start_y = clock_cy + clock_r + 15
        text_area_h  = H - 110 - text_start_y
        line_h       = 95
        total_h      = n * line_h
        ty           = max(clock_cy + clock_r + 40,
                           text_start_y + (text_area_h - total_h) // 2 + 10)

    for i, line in enumerate(time_lines):
        f = font_large
        while True:
            bbox = draw.textbbox((0, 0), line, font=f)
            if (bbox[2] - bbox[0]) < (W - 60):
                break
            current_size = getattr(f, "size", 100)
            if current_size <= 40:
                break
            f = get_font(int(current_size) - 6, fn)
        draw.text((W // 2, ty + i * line_h), line, font=f, fill=0, anchor="mm")

    sep_y = H - 105
    draw.line([(PAD2 + 8, sep_y), (W - PAD2 - 8, sep_y)], fill=0, width=1)
    bar_cy    = H - 52
    bar_left  = PAD2 + 8
    bar_right = W - PAD2 - 8
    bar_width = bar_right - bar_left
    div_x     = bar_left + bar_width // 3
    div_x2    = bar_left + 2 * bar_width // 3
    draw.line([(div_x,  H - 92), (div_x,  H - 15)], fill=0, width=1)
    draw.line([(div_x2, H - 92), (div_x2, H - 15)], fill=0, width=1)

    day_name  = _adapt_text(DAYS_HE[now.weekday()], fn)
    if jewish_date:
        jewish_date = _adapt_text(jewish_date, fn)
    if jewish_date and "\n" in jewish_date:
        date_str, year_str = jewish_date.split("\n", 1)
    else:
        date_str = jewish_date if jewish_date else _adapt_text(f"{now.day} {MONTHS_HE[now.month - 1]}", fn)
        year_str = None
    left_cx   = (bar_left + div_x) // 2
    cell_w    = div_x - bar_left - 10

    def _fit_font(text: str, start: int, minimum: int = 18,
                  max_w: int = cell_w) -> ImageFont.FreeTypeFont:
        f = get_font(start, fn)
        while True:
            bbox = draw.textbbox((0, 0), text, font=f)
            if (bbox[2] - bbox[0]) <= max_w:
                return f
            cur = getattr(f, "size", start)
            if cur <= minimum:
                return f
            f = get_font(cur - 2, fn)

    if year_str:
        day_font  = _fit_font(day_name, 28)
        date_font = _fit_font(date_str, 26)
        year_font = _fit_font(year_str, 22)
        draw.text((left_cx, bar_cy - 26), day_name, font=day_font,  fill=0, anchor="mm")
        draw.text((left_cx, bar_cy),      date_str, font=date_font, fill=0, anchor="mm")
        draw.text((left_cx, bar_cy + 24), year_str, font=year_font, fill=0, anchor="mm")
    else:
        date_font = _fit_font(date_str, 34)
        draw.text((left_cx, bar_cy - 14), day_name, font=font_small, fill=0, anchor="mm")
        draw.text((left_cx, bar_cy + 14), date_str, font=date_font,  fill=0, anchor="mm")

    mid_x = (div_x + div_x2) // 2
    if period_line:
        draw.text((mid_x, bar_cy), period_line, font=font_small, fill=0, anchor="mm")

    if weather:
        right_start = div_x2
        right_end   = W - PAD2 - 8
        icon_x      = right_start + (right_end - right_start) // 4
        text_x      = right_start + 3 * (right_end - right_start) // 4
        _draw_weather_icon(draw, icon_x, bar_cy, weather.get("icon_key", "cloud"), size=34)
        _draw_temperature(draw, text_x, bar_cy - 14, weather["temp"], get_font(40, fn), fn)
        desc = _adapt_text(weather.get("desc", ""), fn)
        # Keep the description inside its half of the cell, clear of the icon
        desc_w = 2 * (right_end - text_x) - 8
        draw.text((text_x, bar_cy + 16), desc,
                  font=_fit_font(desc, 34, minimum=16, max_w=desc_w), fill=0, anchor="mm")

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