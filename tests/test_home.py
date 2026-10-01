import pytest
from fastapi.testclient import TestClient
from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_home_returns_login_page(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert 'action="/login"' in resp.text


def test_config_requires_login(client):
    resp = client.get("/config", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/"


def test_clock_png_still_works(client):
    resp = client.get("/clock.png")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"


def test_clock_path_still_works(client):
    resp = client.get("/clock")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"


def test_clock_png_blank(client):
    resp = client.get("/clock.png?blank=1")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"


def test_tailwind_css_served(client):
    resp = client.get("/static/tailwind.min.css")
    assert resp.status_code == 200
    assert "text/css" in resp.headers.get("content-type", "")
