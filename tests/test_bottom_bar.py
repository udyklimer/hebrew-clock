import datetime
import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image, features

from app import db
from app.api.v1 import router as router_module
from app.main import app
from app.services import clock, languages

W, H = clock.EPD_WIDTH, clock.EPD_HEIGHT
_SEPARATOR_Y = H - 105


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def _pixels(png: bytes):
    return Image.open(io.BytesIO(png)).convert("L").load()


def _render(**kw) -> bytes:
    return clock.generate_clock_image(
        weather={"temp": 21, "icon_key": "sun"},
        now=datetime.datetime(2026, 10, 23, 11, 17), **kw)


def test_hidden_bottom_row_has_no_lines():
    shown, hidden = _pixels(_render()), _pixels(_render(bottom_bar=False))
    # The separator line runs across the image only when the row is shown
    assert shown[W // 2 - 300, _SEPARATOR_Y] == 0
    assert hidden[W // 2 - 300, _SEPARATOR_Y] == 255


@pytest.mark.skipif(not features.check("raqm"), reason="needs Raqm text shaping")
@pytest.mark.parametrize("language, font_name", [
    (code, font) for code in languages.LANGUAGES for font in clock.fonts_for_language(code)])
@pytest.mark.parametrize("clock_style", sorted(clock.VALID_CLOCK_STYLES))
def test_text_stays_inside_the_frame_without_bottom_row(language, font_name, clock_style):
    px = _pixels(_render(font_name=font_name, clock_style=clock_style, language=language,
                         bottom_bar=False))
    # The strip just inside the outer frame stays white all around
    for x0, y0, x1, y1 in ((12, 12, W - 13, 14), (12, H - 14, W - 13, H - 13),
                           (12, 12, 14, H - 13), (W - 15, 12, W - 13, H - 13)):
        assert all(px[x, y] >= 128 for x in range(x0, x1 + 1) for y in range(y0, y1 + 1))


def test_setting_saved_and_weather_not_fetched(client, monkeypatch):
    client.cookies.clear()
    client.post("/register", data={"username": "nobottom", "password": "correct-horse"})
    client.post("/config", data={"font": clock.DEFAULT_FONT, "location": "Haifa",
                                 "calendar": "jewish", "bottom_bar": "0"})
    assert db.get_user_settings("nobottom")["bottom_bar"] == "0"
    assert 'value="0" selected' in client.get("/config").text

    async def must_not_be_called(*args, **kwargs):
        raise AssertionError("fetched although the bottom row is hidden")

    monkeypatch.setattr(router_module.weather_svc, "get_weather", must_not_be_called)
    monkeypatch.setattr(router_module.jewish_cal_svc, "get_jewish_date", must_not_be_called)
    assert client.get("/clock.png?user=nobottom").status_code == 200
