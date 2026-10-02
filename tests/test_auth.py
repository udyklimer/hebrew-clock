import datetime
import time

import pytest
from fastapi.testclient import TestClient

from app import db
from app.core import security
from app.main import app
from app.services import clock

PASSWORD = "correct-horse"


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def _register(client, username, password=PASSWORD):
    return client.post(
        "/register",
        data={"username": username, "password": password},
        follow_redirects=False,
    )


# ── Passwords ─────────────────────────────────────────

def test_password_is_stored_hashed(client):
    assert _register(client, "hashuser").status_code == 303
    with db.get_db() as conn:
        stored = conn.execute(
            "SELECT password FROM users WHERE username = 'hashuser'"
        ).fetchone()["password"]
    assert PASSWORD not in stored
    assert security.is_hashed(stored)


def test_authenticate_accepts_only_right_password(client):
    _register(client, "authuser")
    assert db.authenticate_user("authuser", PASSWORD)
    assert not db.authenticate_user("authuser", PASSWORD + "x")
    assert not db.authenticate_user("nosuchuser", PASSWORD)


def test_legacy_plaintext_password_is_migrated():
    db.init_db()
    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO users (username, password) VALUES ('legacy', 'oldplain1')"
        )
    assert db.authenticate_user("legacy", "oldplain1")
    with db.get_db() as conn:
        stored = conn.execute(
            "SELECT password FROM users WHERE username = 'legacy'"
        ).fetchone()["password"]
    assert security.is_hashed(stored)


def test_register_rejects_short_password(client):
    resp = _register(client, "shortpw", "abc")
    assert resp.status_code == 200
    assert "at least" in resp.text
    assert not db.authenticate_user("shortpw", "abc")


def test_register_rejects_duplicate(client):
    _register(client, "dupuser")
    resp = _register(client, "dupuser")
    assert resp.status_code == 200
    assert "already taken" in resp.text


# ── Sessions ──────────────────────────────────────────

def test_login_then_config(client):
    _register(client, "flowuser")
    client.cookies.clear()
    resp = client.post(
        "/login",
        data={"username": "FlowUser", "password": PASSWORD},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    resp = client.get("/config")
    assert resp.status_code == 200
    assert "flowuser" in resp.text


def test_wrong_password_sets_no_cookie(client):
    _register(client, "wrongpw")
    client.cookies.clear()
    resp = client.post(
        "/login", data={"username": "wrongpw", "password": "nope-nope"}
    )
    assert "Invalid username or password" in resp.text
    assert security.SESSION_COOKIE not in client.cookies


def test_forged_cookie_is_rejected(client):
    _register(client, "victim")
    client.cookies.clear()
    client.cookies.set(security.SESSION_COOKIE, "victim")
    assert client.get("/config", follow_redirects=False).status_code == 303
    resp = client.post(
        "/config",
        data={"font": "DavidLibre-Bold", "location": "Eilat", "calendar": "jewish"},
        follow_redirects=False,
    )
    assert resp.headers["location"] == "/"
    assert db.get_user_settings("victim")["location"] == "Haifa"


def test_tampered_and_expired_sessions():
    token = security.create_session("alice")
    assert security.read_session(token) == "alice"
    assert security.read_session(token.replace("alice", "bob", 1)) is None
    payload = f"alice.{int(time.time()) - 10}"
    assert security.read_session(f"{payload}.{security._sign(payload)}") is None
    assert security.read_session("garbage") is None
    assert security.read_session(None) is None


def test_save_config_sanitizes_input(client):
    _register(client, "cfguser")
    resp = client.post(
        "/config",
        data={"font": "NoSuchFont", "location": "  Eilat ", "calendar": "martian",
              "sleeptime": "7", "blank": "1"},
        follow_redirects=False,
    )
    assert resp.headers["location"] == "/config?saved=1"
    assert db.get_user_settings("cfguser") == {
        "font": clock.DEFAULT_FONT, "location": "Eilat", "calendar": "gregorian",
        "sleeptime": "0", "blank": "1", "clock_style": "analog",
        "sleep_start": "22:00", "sleep_end": "06:00",
        "battery_display": "none", "battery_position": "left",
    }


# ── Israel time / DST ─────────────────────────────────

@pytest.mark.parametrize("utc, expected_hour", [
    ((2026, 1, 15, 12, 0), 14),   # winter, UTC+2
    ((2026, 7, 15, 12, 0), 15),   # summer, UTC+3
    ((2026, 3, 10, 12, 0), 14),   # March, before the change (Fri 27 Mar 2026)
    ((2026, 3, 27, 12, 0), 15),   # first day of summer time
    ((2026, 10, 24, 12, 0), 15),  # last full day of summer time
    ((2026, 10, 26, 12, 0), 14),  # after the change (Sun 25 Oct 2026)
])
def test_israel_utc_offset(utc, expected_hour):
    moment = datetime.datetime(*utc, tzinfo=datetime.timezone.utc)
    assert moment.astimezone(clock.ISRAEL_TZ).hour == expected_hour


def test_get_israel_time_is_naive_and_current():
    now = clock.get_israel_time()
    assert now.tzinfo is None
    expected = datetime.datetime.now(clock.ISRAEL_TZ).replace(tzinfo=None)
    assert abs((now - expected).total_seconds() - clock.settings.display_lag) < 5


# ── Config page preview ───────────────────────────────

def test_config_page_has_live_preview(client):
    _register(client, "previewuser")
    resp = client.get("/config")
    assert 'id="preview"' in resp.text
    assert 'id="clock-url"' in resp.text
    assert "previewuser" in resp.text  # shown next to the server URL


def test_preview_params_override_saved_settings(client):
    _register(client, "overrideuser")
    db.update_user_settings("overrideuser", "DavidLibre-Bold", "Haifa", "gregorian", "0", "1")
    blank = client.get("/clock.png?user=overrideuser").content
    shown = client.get(
        "/clock.png?user=overrideuser&font=DavidLibre-Bold&location=Haifa"
        "&calendar=gregorian&sleeptime=0&blank=0"
    )
    assert shown.headers["content-type"] == "image/png"
    assert shown.content != blank


# ── Clock style ───────────────────────────────────────

def test_clock_style_is_saved_and_validated(client):
    _register(client, "styleuser")
    form = {"font": clock.DEFAULT_FONT, "location": "Haifa", "calendar": "gregorian"}
    client.post("/config", data={**form, "clock_style": "digital"})
    assert db.get_user_settings("styleuser")["clock_style"] == "digital"
    assert 'value="digital" selected' in client.get("/config").text
    client.post("/config", data={**form, "clock_style": "sundial"})
    assert db.get_user_settings("styleuser")["clock_style"] == "analog"


def test_clock_styles_render_differently(client):
    images = {
        style: client.get(f"/clock.png?clock_style={style}").content
        for style in ("analog", "digital", "none")
    }
    assert len(set(images.values())) == 3
    assert client.get("/clock.png?clock_style=bogus").status_code == 200


# ── Sleep window ──────────────────────────────────────

def _at(monkeypatch, hour, minute=0):
    monkeypatch.setattr(clock, "get_israel_time",
                        lambda: datetime.datetime(2026, 10, 1, hour, minute))


@pytest.mark.parametrize("start, end, hour, minute, expected", [
    ("22:00", "06:00", 23, 0, True),    # overnight window, before midnight
    ("22:00", "06:00", 3, 30, True),    # overnight window, after midnight
    ("22:00", "06:00", 6, 0, False),    # end time is exclusive
    ("22:00", "06:00", 21, 59, False),
    ("13:00", "15:30", 15, 29, True),   # same-day window
    ("13:00", "15:30", 15, 30, False),
    ("08:00", "08:00", 12, 0, True),    # equal times: all day
    (None, None, 12, 0, True),          # no window: all day
    ("25:00", "06:00", 12, 0, True),    # invalid time: all day
])
def test_in_sleep_window(monkeypatch, start, end, hour, minute, expected):
    _at(monkeypatch, hour, minute)
    assert clock.in_sleep_window(start, end) is expected


def test_sleep_times_are_saved_and_validated(client):
    _register(client, "sleepuser")
    form = {"font": clock.DEFAULT_FONT, "location": "Haifa", "calendar": "gregorian", "sleeptime": "1"}
    client.post("/config", data={**form, "sleep_start": "23:15", "sleep_end": "07:45"})
    cfg = db.get_user_settings("sleepuser")
    assert (cfg["sleeptime"], cfg["sleep_start"], cfg["sleep_end"]) == ("1", "23:15", "07:45")
    page = client.get("/config").text
    assert 'value="23:15"' in page and 'value="07:45"' in page
    client.post("/config", data={**form, "sleep_start": "9pm", "sleep_end": "24:00"})
    cfg = db.get_user_settings("sleepuser")
    assert (cfg["sleep_start"], cfg["sleep_end"]) == ("22:00", "06:00")


def test_night_image_only_inside_window(client, monkeypatch):
    _register(client, "nightuser")
    db.update_user_settings("nightuser", clock.DEFAULT_FONT, "Haifa", "gregorian", "1",
                            sleep_start="22:00", sleep_end="06:00")
    night = clock.generate_clock_image(sleep_time=True)

    _at(monkeypatch, 23)
    assert client.get("/clock.png?user=nightuser").content == night
    _at(monkeypatch, 12)
    assert client.get("/clock.png?user=nightuser").content != night
    # Sleep mode off: never the night image, even inside the window
    db.update_user_settings("nightuser", clock.DEFAULT_FONT, "Haifa", "gregorian", "0")
    _at(monkeypatch, 23)
    assert client.get("/clock.png?user=nightuser").content != night


def test_explicit_sleeptime_param(client, monkeypatch):
    night = clock.generate_clock_image(sleep_time=True)
    _at(monkeypatch, 12)
    # Without a window in the request, sleeptime=1 means now (older devices)
    assert client.get("/clock.png?sleeptime=1").content == night
    # With a window (the settings preview), the hour decides
    url = "/clock.png?sleeptime=1&sleep_start=22:00&sleep_end=06:00"
    assert client.get(url).content != night
    _at(monkeypatch, 2)
    assert client.get(url).content == night


# ── Battery indicator ─────────────────────────────────

@pytest.mark.parametrize("millivolts, expected", [
    (4300, 100), (4200, 100), (4063, 84), (3840, 50), (3690, 10), (3270, 0), (2500, 0),
])
def test_battery_percent_from_mv(millivolts, expected):
    assert clock.battery_percent_from_mv(millivolts) == expected


def test_battery_setting_is_saved_and_validated(client):
    _register(client, "battuser")
    form = {"font": clock.DEFAULT_FONT, "location": "Haifa", "calendar": "gregorian"}
    client.post("/config", data={**form, "battery_display": "both", "battery_position": "right"})
    cfg = db.get_user_settings("battuser")
    assert (cfg["battery_display"], cfg["battery_position"]) == ("both", "right")
    client.post("/config", data={**form, "battery_display": "huge", "battery_position": "middle"})
    cfg = db.get_user_settings("battuser")
    assert (cfg["battery_display"], cfg["battery_position"]) == ("none", "left")


def test_battery_is_drawn_only_when_reported_and_enabled(client, monkeypatch):
    _at(monkeypatch, 12)
    _register(client, "battdraw")
    base = "/clock.png?user=battdraw"
    plain = client.get(base).content
    # Setting is "none": a reported battery changes nothing
    assert client.get(base + "&battery_mv=3900").content == plain
    db.update_user_settings("battdraw", clock.DEFAULT_FONT, "Haifa", "gregorian", "0",
                            battery_display="both", battery_position="left")
    # Enabled but nothing reported: still nothing drawn
    assert client.get(base).content == plain
    shown = client.get(base + "&battery_mv=3900").content
    assert shown != plain
    assert client.get(base + "&battery_mv=3900&charging=1").content != shown
    assert client.get(base + "&battery=64").content == shown  # 3900 mV is 64%
    assert client.get(base + "&battery=64&battery_position=right").content != shown
    assert client.get(base + "&battery=150").status_code == 422
