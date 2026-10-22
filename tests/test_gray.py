import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import db
from app.main import app
from app.services import clock


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def _shades(png: bytes) -> set[int]:
    return set(Image.open(io.BytesIO(png)).convert("L").getdata())


def test_black_and_white_by_default(client):
    resp = client.get("/clock.png")
    assert resp.headers["x-gray-levels"] == "2"
    assert _shades(resp.content) <= {0, 255}


def test_gray_uses_the_four_shades(client):
    resp = client.get("/clock.png?gray=1")
    assert resp.headers["x-gray-levels"] == "4"
    image = Image.open(io.BytesIO(resp.content))
    assert image.mode == "P"
    shades = _shades(resp.content)
    assert shades <= set(clock.GRAY_LEVELS)
    assert len(shades) == 4                    # smoothed edges use the middle shades


def test_gray_night_image(client):
    png = clock.generate_clock_image(sleep_time=True, gray=True)
    assert _shades(png) <= set(clock.GRAY_LEVELS)


def test_gray_setting_is_saved_and_used(client):
    client.cookies.clear()
    client.post("/register", data={"username": "grayuser", "password": "correct-horse"})
    client.post("/config", data={"font": clock.DEFAULT_FONT, "location": "Haifa",
                                 "calendar": "gregorian", "grayscale": "1"})
    assert db.get_user_settings("grayuser")["grayscale"] == "1"
    assert 'value="1" selected' in client.get("/config").text
    assert client.get("/clock.png?user=grayuser").headers["x-gray-levels"] == "4"
    # The preview can override it
    assert client.get("/clock.png?user=grayuser&gray=0").headers["x-gray-levels"] == "2"
