"""Checks that no font draws text across the frame, the separator or the cell dividers."""
import datetime
import io

import pytest
from PIL import Image, features

from app.services import clock

W, H = clock.EPD_WIDTH, clock.EPD_HEIGHT

# Strips that must stay white: just inside the outer frame, either side of the
# separator line, and either side of the two bottom-bar dividers.
_BAR_LEFT, _BAR_WIDTH = 24, 752
_CLEAR_STRIPS = [
    (12, 12, W - 13, 14), (12, H - 14, W - 13, H - 13),
    (12, 12, 14, H - 13), (W - 15, 12, W - 13, H - 13),
    (18, 372, W - 19, 373), (18, 377, W - 19, 378),
]
for _dx in (_BAR_LEFT + _BAR_WIDTH // 3, _BAR_LEFT + 2 * _BAR_WIDTH // 3):
    _CLEAR_STRIPS += [(_dx - 3, 390, _dx - 2, 462), (_dx + 2, 390, _dx + 3, 462)]

_WEATHER = {"temp": -3, "icon_key": "sun_cloud", "desc": "מְעֻנָּן חֶלְקִי"}
_JEWISH_DATE = "כ״ז בְּסִיוָן\nתשפ״ו"


@pytest.mark.skipif(not features.check("raqm"), reason="needs Raqm text shaping")
@pytest.mark.parametrize("font_name", sorted(clock.VALID_FONTS))
@pytest.mark.parametrize("clock_style", sorted(clock.VALID_CLOCK_STYLES))
def test_text_stays_inside_its_area(monkeypatch, font_name, clock_style):
    for hour, minute, jewish_date in ((22, 42, None), (11, 17, _JEWISH_DATE)):
        monkeypatch.setattr(
            clock, "get_israel_time",
            lambda h=hour, m=minute: datetime.datetime(2026, 9, 1, h, m),
        )
        png = clock.generate_clock_image(
            font_name=font_name, weather=_WEATHER,
            jewish_date=jewish_date, clock_style=clock_style,
            battery=100, charging=True, battery_display="both",
            battery_position="left" if jewish_date else "right",
        )
        pixels = Image.open(io.BytesIO(png)).convert("L").load()
        for x0, y0, x1, y1 in _CLEAR_STRIPS:
            assert all(
                pixels[x, y] >= 128
                for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)
            ), f"text crosses a line near {(x0, y0, x1, y1)} at {hour}:{minute:02d}"


# ── Fonts without nikud ───────────────────────────────

_NO_NIKUD_FONT = "ankaclm-bold-webfont"


@pytest.mark.skipif(_NO_NIKUD_FONT not in clock.VALID_FONTS, reason="font not bundled")
@pytest.mark.parametrize("pointed, expected", [
    ("מְעֻנָּן חֶלְקִי", "מעונן חלקי"),
    ("בַּבֹּקֶר", "בבוקר"),
    ("לִפְנוֹת בֹּקֶר", "לפנות בוקר"),
    ("אַחַר הַצָּהֳרַיִם", "אחר הצהריים"),
    ("שְׁתַּיִם וַחֲמִשִּׁים וּשְׁתַּיִם", "שתיים וחמישים ושתיים"),
    ("שְׁתֵּים עֶשְׂרֵה וּשְׁתֵּים עֶשְׂרֵה דַּקּוֹת", "שתים עשרה ושתים עשרה דקות"),
    ("כ״ז בְּסִיוָן\nתשפ״ו", "כ\"ז בסיוון\nתשפ\"ו"),
    ("שֶׁבַע וָרֶבַע", "שבע ורבע"),
])
def test_unpointed_fonts_use_full_spelling(pointed, expected):
    assert clock._adapt_text(pointed, _NO_NIKUD_FONT) == expected


def test_pointed_fonts_keep_their_text():
    assert clock._adapt_text("מְעֻנָּן", "NotoSansHebrew-Bold") == "מְעֻנָּן"
