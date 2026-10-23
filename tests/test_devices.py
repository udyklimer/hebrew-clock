import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.services import clock

FORM = {"font": clock.DEFAULT_FONT, "location": "Haifa", "calendar": "gregorian"}


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def _register(client, username):
    client.cookies.clear()
    client.post("/register", data={"username": username, "password": "correct-horse"})


def test_device_is_recorded_with_its_reports(client):
    _register(client, "devuser")
    client.get("/clock.png?user=devuser&device=AABBCC112233&fw=1.0.3&battery_mv=3900&charging=1")
    [device] = db.list_devices("devuser")
    assert device["device_id"] == "aabbcc112233"          # stored in lower case
    assert (device["fw"], device["battery_mv"], device["charging"]) == ("1.0.3", 3900, True)
    assert device["last_seen"] is not None and device["settings"] is None


def test_two_devices_of_one_user_are_kept_apart(client):
    _register(client, "twodev")
    client.get("/clock.png?user=twodev&device=kitchen01&fw=1.0.2")
    client.get("/clock.png?user=twodev&device=bedroom02&fw=1.0.3")
    versions = {d["device_id"]: d["fw"] for d in db.list_devices("twodev")}
    assert versions == {"kitchen01": "1.0.2", "bedroom02": "1.0.3"}


def test_requests_without_device_or_bad_id_record_nothing(client):
    _register(client, "nodev")
    client.get("/clock.png?user=nodev&fw=1.0.0")
    client.get("/clock.png?user=nodev&device=bad id!")
    client.get("/clock.png?user=nosuchuser&device=ghost01")   # unknown user
    assert db.list_devices("nodev") == []
    assert db.list_devices("nosuchuser") == []


def test_device_moving_to_another_user_starts_fresh(client):
    _register(client, "firstowner")
    _register(client, "secondowner")
    client.get("/clock.png?user=firstowner&device=mover01")
    db.rename_device("firstowner", "mover01", "Kitchen")
    db.set_device_settings("firstowner", "mover01", {"language": "fr"})
    client.get("/clock.png?user=secondowner&device=mover01")
    assert db.list_devices("firstowner") == []
    [device] = db.list_devices("secondowner")
    assert device["name"] is None and device["settings"] is None


def test_moved_device_leaves_no_version_behind(client):
    _register(client, "oldhome")
    client.get("/clock.png?user=oldhome&device=wanderer&fw=1.0.4")
    assert db.get_device_firmware("oldhome")[0] == "1.0.4"
    _register(client, "newhome")
    client.get("/clock.png?user=newhome&device=wanderer&fw=1.0.4")
    assert db.get_device_firmware("oldhome") == (None, None)
    client.cookies.clear()
    client.post("/login", data={"username": "oldhome", "password": "correct-horse"})
    assert "המכשיר עדיין לא דיווח" in client.get("/config").text   # "hasn't reported yet"


def test_device_limit_per_user(client):
    _register(client, "manydev")
    for i in range(db.MAX_DEVICES_PER_USER + 3):
        client.get(f"/clock.png?user=manydev&device=dev{i:04d}")
    assert len(db.list_devices("manydev")) == db.MAX_DEVICES_PER_USER


def test_own_settings_apply_only_to_that_device(client):
    _register(client, "ownset")
    client.get("/clock.png?user=ownset&device=aaaa1111")
    client.get("/clock.png?user=ownset&device=bbbb2222")
    client.post("/config", data={**FORM, "clock_style": "digital", "batch_minutes": "1"})
    resp = client.post("/config", data={**FORM, "device": "aaaa1111", "own_settings": "1",
                                        "clock_style": "none", "batch_minutes": "7"},
                       follow_redirects=False)
    assert resp.headers["location"] == "/config?device=aaaa1111&saved=1"
    assert db.get_effective_settings("ownset", "aaaa1111")["clock_style"] == "none"
    assert db.get_effective_settings("ownset", "bbbb2222")["clock_style"] == "digital"
    assert db.get_effective_settings("ownset")["clock_style"] == "digital"
    # The device's own settings reach the image request too
    assert client.get("/clock.png?user=ownset&device=aaaa1111").headers["x-batch-minutes"] == "7"
    assert client.get("/clock.png?user=ownset&device=bbbb2222").headers["x-batch-minutes"] == "1"


def test_turning_own_settings_off_returns_to_shared(client):
    _register(client, "backshared")
    client.get("/clock.png?user=backshared&device=cccc3333")
    client.get("/clock.png?user=backshared&device=cccc4444")
    client.post("/config", data={**FORM, "device": "cccc3333", "own_settings": "1", "language": "es"})
    assert db.get_effective_settings("backshared", "cccc3333")["language"] == "es"
    client.post("/config", data={**FORM, "device": "cccc3333", "language": "es"})  # box unticked
    assert db.get_device("backshared", "cccc3333")["settings"] is None
    assert db.get_effective_settings("backshared", "cccc3333")["language"] == "he"


def test_cannot_touch_another_users_device(client):
    _register(client, "owner1")
    client.get("/clock.png?user=owner1&device=private1")
    _register(client, "intruder")
    client.post("/config", data={**FORM, "device": "private1", "own_settings": "1", "language": "fr"})
    client.post("/devices/private1/rename", data={"name": "Mine now"})
    client.post("/devices/private1/delete")
    [device] = db.list_devices("owner1")
    assert device["settings"] is None and device["name"] is None
    # The intruder's own (shared) settings were saved instead
    assert db.get_user_settings("intruder")["language"] == "fr"


def test_rename_and_remove(client):
    _register(client, "renamer")
    client.get("/clock.png?user=renamer&device=dddd4444")
    client.get("/clock.png?user=renamer&device=dddd5555")
    client.post("/devices/dddd4444/rename", data={"name": "  Kitchen  "})
    assert db.get_device("renamer", "dddd4444")["name"] == "Kitchen"
    page = client.get("/config").text
    assert "Kitchen" in page and "dddd4444" in page
    client.post("/devices/dddd4444/delete")
    assert [d["device_id"] for d in db.list_devices("renamer")] == ["dddd5555"]


def test_single_device_is_not_listed(client):
    _register(client, "onlyone")
    client.get("/clock.png?user=onlyone&device=ffff6666&fw=1.0.1")
    page = client.get("/config").text
    assert 'id="settings-target"' not in page and 'class="url-card devices-card"' not in page
    assert "1.0.1" in page                    # its version still shows
    # Old own settings are ignored while it is the only device
    db.set_device_settings("onlyone", "ffff6666", {"language": "fr"})
    assert db.get_effective_settings("onlyone", "ffff6666")["language"] == "he"
    client.post("/config", data={**FORM, "device": "ffff6666", "own_settings": "1", "language": "es"})
    assert db.get_user_settings("onlyone")["language"] == "es"   # saved as shared settings


def test_settings_page_for_one_device(client):
    _register(client, "pagedev")
    client.get("/clock.png?user=pagedev&device=eeee5555&fw=1.0.1&battery_mv=3900")
    client.get("/clock.png?user=pagedev&device=eeee6666")
    page = client.get("/config?device=eeee5555").text
    assert 'name="device" value="eeee5555"' in page
    assert 'id="own_settings"' in page
    assert "64%" in page                      # 3900 mV shown as a percentage
    # Another user's device ID in the URL just shows the shared settings
    _register(client, "otherpage")
    assert 'name="device"' not in client.get("/config?device=eeee5555").text
