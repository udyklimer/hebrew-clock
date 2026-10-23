import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import firmware

FULL_IMAGE = b"\xe9" + b"full flash image" * 200
OTA_IMAGE = b"\xe9" + b"ota app image" * 100
PAGE = b"<!doctype html><esp-web-install-button manifest=\"/install/manifest.json\">"
MANIFEST = {"name": "Hebrew Clock", "version": "LATEST_RELEASE_VERSION",
            "builds": [{"chipFamily": "ESP32-S3", "parts": [{"path": "firmware-full.bin", "offset": 0}]}]}


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def _clean():
    firmware._latest.clear()
    firmware.update_headers_for.cache_clear()
    folder = firmware.firmware_dir()
    if folder.exists():
        for path in folder.glob("*"):
            path.unlink()


@pytest.fixture()
def release(monkeypatch):
    """A fake latest release v1.0.5 with a full image; records what is downloaded."""
    downloads = []

    async def fake_fetch(client):
        return {"version": "1.0.5", "tag": "v1.0.5",
                "bin_url": "https://gh/firmware.bin", "sig_url": "https://gh/firmware.bin.sig",
                "full_url": "https://gh/firmware-full.bin"}

    async def fake_download(client, url):
        downloads.append(url)
        if url.endswith("/install/index.html"):
            assert "/v1.0.5/" in url            # taken from the release's tag
            return PAGE
        if url.endswith("/install/manifest.json"):
            return json.dumps(MANIFEST).encode()
        return {"https://gh/firmware-full.bin": FULL_IMAGE, "https://gh/firmware.bin": OTA_IMAGE,
                "https://gh/firmware.bin.sig": b"sig"}[url]

    monkeypatch.setattr(firmware, "_fetch_latest_release", fake_fetch)
    monkeypatch.setattr(firmware, "_download", fake_download)
    _clean()
    yield downloads
    _clean()


def test_install_page_manifest_and_image(client, release):
    page = client.get("/install")
    assert page.status_code == 200 and "text/html" in page.headers["content-type"]
    assert page.content == PAGE                     # served unchanged

    manifest = client.get("/install/manifest.json")
    assert manifest.headers["content-type"].startswith("application/json")
    assert manifest.json()["version"] == "1.0.5"    # filled in from the release
    assert manifest.json()["builds"] == MANIFEST["builds"]

    image = client.get("/install/firmware-full.bin")
    assert image.headers["content-type"] == "application/octet-stream"
    assert image.content == FULL_IMAGE              # served unchanged


def test_installer_files_downloaded_once(client, release):
    for path in ("/install", "/install/manifest.json", "/install/firmware-full.bin", "/install/"):
        assert client.get(path).status_code == 200
    assert len(release) == 3


def test_ota_still_uses_the_app_image(client, release):
    resp = client.get("/clock.png?fw=1.0.4")
    assert resp.headers["x-firmware-version"] == "1.0.5"
    assert client.get(resp.headers["x-firmware-url"]).content == OTA_IMAGE


def test_installer_unavailable_without_full_image(client, monkeypatch):
    async def old_release(client):
        return {"version": "1.0.4", "tag": "v1.0.4", "bin_url": "b", "sig_url": "s", "full_url": None}

    monkeypatch.setattr(firmware, "_fetch_latest_release", old_release)
    _clean()
    page = client.get("/install")
    assert page.status_code == 503 and "isn't available" in page.text
    assert client.get("/install/manifest.json").status_code == 503
    assert client.get("/install/firmware-full.bin").status_code == 503
    _clean()


def test_pages_link_to_the_installer(client):
    client.cookies.clear()
    assert 'href="/install"' in client.get("/").text
