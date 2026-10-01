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
        "sleeptime": "0", "blank": "1",
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
