import base64
import hashlib

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app import db
from app.services import firmware

FIRMWARE = b"\xe9" + b"fake firmware image" * 100
SIGNATURE = b"0E\x02 fake DER signature"


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def release(monkeypatch):
    """A fake latest release 1.5.0, with its files served by a fake GitHub."""
    calls = {"api": 0, "downloads": 0}

    async def fake_fetch(client):
        calls["api"] += 1
        return {"version": "1.5.0", "bin_url": "https://gh/bin", "sig_url": "https://gh/sig"}

    async def fake_download(client, url):
        calls["downloads"] += 1
        return FIRMWARE if url.endswith("bin") else SIGNATURE

    monkeypatch.setattr(firmware, "_fetch_latest_release", fake_fetch)
    monkeypatch.setattr(firmware, "_download", fake_download)
    firmware._latest.clear()
    firmware.update_headers_for.cache_clear()
    for path in firmware.firmware_dir().glob("*") if firmware.firmware_dir().exists() else []:
        path.unlink()
    yield calls
    firmware._latest.clear()
    firmware.update_headers_for.cache_clear()


@pytest.mark.parametrize("value, expected", [
    ("1.4.0", (1, 4, 0)), ("v2.10.3", (2, 10, 3)), (" 1.0.0 ", (1, 0, 0)),
    ("1.4", None), ("latest", None), ("", None), (None, None),
])
def test_parse_version(value, expected):
    assert firmware.parse_version(value) == expected


def test_older_device_is_offered_the_update(client, release):
    resp = client.get("/clock.png?fw=1.4.0")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.headers["x-firmware-version"] == "1.5.0"
    assert resp.headers["x-firmware-url"] == "/firmware/1.5.0.bin"
    assert resp.headers["x-firmware-size"] == str(len(FIRMWARE))
    assert resp.headers["x-firmware-sha256"] == hashlib.sha256(FIRMWARE).hexdigest()
    assert base64.b64decode(resp.headers["x-firmware-signature"]) == SIGNATURE

    download = client.get(resp.headers["x-firmware-url"])
    assert download.status_code == 200
    assert download.content == FIRMWARE
    assert download.headers["content-length"] == str(len(FIRMWARE))


@pytest.mark.parametrize("query", ["?fw=1.5.0", "?fw=2.0.0", "?fw=garbage", ""])
def test_no_offer_when_current_newer_or_unknown(client, release, query):
    resp = client.get("/clock.png" + query)
    assert resp.status_code == 200
    assert "x-firmware-version" not in resp.headers


def test_release_checked_and_downloaded_once(client, release):
    for _ in range(3):
        client.get("/clock.png?fw=1.0.0")
    assert release == {"api": 1, "downloads": 2}  # one API call; .bin and .sig once


def test_offer_also_on_blank_image(client, release):
    resp = client.get("/clock.png?fw=1.0.0&blank=1")
    assert resp.headers["x-firmware-version"] == "1.5.0"


def test_no_releases_means_no_offer(client, monkeypatch):
    async def none(client):
        return None
    monkeypatch.setattr(firmware, "_fetch_latest_release", none)
    firmware._latest.clear()
    resp = client.get("/clock.png?fw=0.0.1")
    assert "x-firmware-version" not in resp.headers
    firmware._latest.clear()


def test_github_failure_does_not_break_the_image(client, monkeypatch):
    async def boom(client):
        raise RuntimeError("GitHub is down")
    monkeypatch.setattr(firmware, "_fetch_latest_release", boom)
    firmware._latest.clear()
    resp = client.get("/clock.png?fw=0.0.1")
    assert resp.status_code == 200
    assert "x-firmware-version" not in resp.headers
    firmware._latest.clear()


@pytest.mark.parametrize("path", ["/firmware/9.9.9.bin", "/firmware/..%2Fclock.db.bin", "/firmware/abc.bin"])
def test_firmware_download_rejects_unknown_files(client, path):
    assert client.get(path).status_code == 404


# ── Settings page ─────────────────────────────────────

def _login(client, username):
    client.cookies.clear()
    client.post("/register", data={"username": username, "password": "correct-horse"})


def test_config_page_before_device_reports(client, release):
    _login(client, "fwfresh")
    page = client.get("/config").text
    assert "המכשיר עדיין לא דיווח" in page
    assert "1.5.0" in page  # latest available


def test_config_page_shows_device_version_and_pending_update(client, release):
    _login(client, "fwold")
    client.get("/clock.png?user=fwold&fw=1.4.0")
    page = client.get("/config").text
    assert ">1.4.0<" in page
    assert "דיווח אחרון" in page
    assert "עדכון ממתין" in page


def test_config_page_up_to_date_device(client, release):
    _login(client, "fwnew")
    client.get("/clock.png?user=fwnew&fw=1.5.0")
    page = client.get("/config").text
    assert ">1.5.0<" in page
    assert "עדכון ממתין" not in page


def test_preview_requests_do_not_overwrite_device_version(client, release):
    _login(client, "fwprev")
    client.get("/clock.png?user=fwprev&fw=1.4.0")
    client.get("/clock.png?user=fwprev&font=DavidLibre-Bold")  # preview: no fw
    assert db.get_device_firmware("fwprev")[0] == "1.4.0"
