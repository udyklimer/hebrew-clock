"""Offers firmware updates to devices, from GitHub Releases of the firmware repo.

A device reports its version with ?fw=<version> on the image request. If the
latest release is newer, the image response carries X-Firmware-* headers and
the device downloads /firmware/<version>.bin from this server.

Each release must have two assets:
  firmware.bin      the application image
  firmware.bin.sig  ECDSA P-256 / SHA-256 signature of firmware.bin (DER)
The device verifies the signature with the public key built into it, so this
server is only a courier: it never needs, and is never trusted with, the key.
"""
import asyncio
import base64
import datetime
import functools
import hashlib
import re

import httpx
from loguru import logger

from app.core.config import settings

CHECK_INTERVAL_SECONDS = 3600
FAIL_BACKOFF_SECONDS = 600
BIN_ASSET, SIG_ASSET = "firmware.bin", "firmware.bin.sig"
MAX_FIRMWARE_BYTES = 8 * 1024 * 1024

_VERSION = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")

# {"release": dict | None, "time": datetime | None, "last_fail": datetime | None}
_latest: dict = {}
_lock = asyncio.Lock()


def parse_version(value: str | None) -> tuple[int, int, int] | None:
    """Parses '1.4.0' or 'v1.4.0' into (1, 4, 0); None if it is not of that form."""
    match = _VERSION.fullmatch(value.strip()) if value else None
    return tuple(int(part) for part in match.groups()) if match else None


def firmware_dir():
    return settings.data_dir / "firmware"


async def _fetch_latest_release(client: httpx.AsyncClient) -> dict | None:
    """Returns {'version', 'bin_url', 'sig_url'} for the latest release, or None."""
    headers = {"Accept": "application/vnd.github+json"}
    if settings.firmware_github_token:
        headers["Authorization"] = f"Bearer {settings.firmware_github_token}"
    resp = await client.get(
        f"https://api.github.com/repos/{settings.firmware_repo}/releases/latest",
        headers=headers,
    )
    if resp.status_code == 404:
        return None  # no releases yet
    resp.raise_for_status()
    data = resp.json()
    version = parse_version(data.get("tag_name"))
    assets = {a["name"]: a["browser_download_url"] for a in data.get("assets", [])}
    if not version or BIN_ASSET not in assets or SIG_ASSET not in assets:
        logger.warning("firmware release {} is missing a version or assets", data.get("tag_name"))
        return None
    return {
        "version": ".".join(map(str, version)),
        "bin_url": assets[BIN_ASSET],
        "sig_url": assets[SIG_ASSET],
    }


async def latest_release(client: httpx.AsyncClient) -> dict | None:
    """The latest release, checked at most once an hour."""
    now = datetime.datetime.utcnow()
    checked = _latest.get("time")
    if checked and (now - checked).total_seconds() < CHECK_INTERVAL_SECONDS:
        return _latest.get("release")
    failed = _latest.get("last_fail")
    if failed and (now - failed).total_seconds() < FAIL_BACKOFF_SECONDS:
        return _latest.get("release")
    try:
        release = await _fetch_latest_release(client)
        _latest.update(release=release, time=now, last_fail=None)
        logger.info("firmware: latest release {}", release["version"] if release else "none")
    except Exception as exc:
        logger.warning("firmware: release check failed: {}", exc)
        _latest["last_fail"] = now
    return _latest.get("release")


async def _download(client: httpx.AsyncClient, url: str) -> bytes:
    resp = await client.get(url, follow_redirects=True, timeout=60.0)
    resp.raise_for_status()
    if len(resp.content) > MAX_FIRMWARE_BYTES:
        raise ValueError(f"{url} is larger than {MAX_FIRMWARE_BYTES} bytes")
    return resp.content


async def _cached_files(release: dict, client: httpx.AsyncClient) -> bool:
    """Makes sure the release's .bin and .sig are in DATA_DIR/firmware."""
    folder = firmware_dir()
    bin_path = folder / f"{release['version']}.bin"
    sig_path = folder / f"{release['version']}.bin.sig"
    if bin_path.exists() and sig_path.exists():
        return True
    async with _lock:
        if bin_path.exists() and sig_path.exists():
            return True
        try:
            firmware = await _download(client, release["bin_url"])
            signature = await _download(client, release["sig_url"])
        except Exception as exc:
            logger.warning("firmware: download of {} failed: {}", release["version"], exc)
            return False
        folder.mkdir(parents=True, exist_ok=True)
        # Write the signature last: a .bin without its .sig is never offered
        bin_path.write_bytes(firmware)
        sig_path.write_bytes(signature)
        logger.info("firmware: cached {} ({} bytes)", release["version"], len(firmware))
        return True


@functools.lru_cache(maxsize=4)
def update_headers_for(version: str) -> dict[str, str]:
    """Headers describing the cached firmware `version`, for the image response."""
    folder = firmware_dir()
    firmware = (folder / f"{version}.bin").read_bytes()
    signature = (folder / f"{version}.bin.sig").read_bytes()
    return {
        "X-Firmware-Version": version,
        "X-Firmware-Url": f"/firmware/{version}.bin",
        "X-Firmware-Size": str(len(firmware)),
        "X-Firmware-Sha256": hashlib.sha256(firmware).hexdigest(),
        "X-Firmware-Signature": base64.b64encode(signature).decode(),
    }


async def check_update(device_version: str | None, client: httpx.AsyncClient) -> dict[str, str]:
    """Returns X-Firmware-* headers if a newer firmware exists, else an empty dict."""
    current = parse_version(device_version)
    if current is None or not settings.firmware_repo:
        return {}
    release = await latest_release(client)
    if not release or parse_version(release["version"]) <= current:
        return {}
    if not await _cached_files(release, client):
        return {}
    return dict(update_headers_for(release["version"]))
