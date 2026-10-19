import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app
from app.services import clock, languages
from app.services.languages import en, es, fr


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


# ── Time phrases ──────────────────────────────────────

@pytest.mark.parametrize("lang, h, m, lines, period", [
    (en, 7, 0, ["seven o'clock"], "in the morning"),
    (en, 7, 15, ["quarter past seven"], "in the morning"),
    (en, 7, 45, ["quarter to eight"], "in the morning"),
    (en, 7, 37, ["twenty-three minutes", "to eight"], "in the morning"),
    (en, 11, 50, ["ten to twelve"], "in the afternoon"),   # period follows the next hour
    (fr, 7, 30, ["sept heures", "et demie"], "du matin"),
    (fr, 7, 45, ["huit heures", "moins le quart"], "du matin"),
    (fr, 7, 37, ["sept heures", "trente-sept"], "du matin"),
    (fr, 12, 30, ["midi et demi"], ""),
    (fr, 13, 1, ["une heure une"], "de l'après-midi"),
    (es, 7, 15, ["las siete y cuarto"], "de la mañana"),
    (es, 7, 50, ["las ocho", "menos diez"], "de la mañana"),
    (es, 13, 5, ["la una y cinco"], "de la tarde"),
    (es, 7, 37, ["las siete", "y treinta y siete"], "de la mañana"),
])
def test_time_phrases(lang, h, m, lines, period):
    assert lang.time_text(h, m) == (lines, period)


def test_every_minute_has_text_in_every_language():
    for code, lang in languages.LANGUAGES.items():
        for h in range(24):
            for m in range(60):
                lines, _ = lang.time_text(h, m)
                assert lines and all(lines), (code, h, m)


# ── Fonts per language ────────────────────────────────

def test_fonts_follow_the_letters_each_language_needs():
    he_fonts = set(clock.fonts_for_language("he"))
    en_fonts = set(clock.fonts_for_language("en"))
    fr_fonts = set(clock.fonts_for_language("fr"))
    assert "Roboto-Bold" in en_fonts and "Roboto-Bold" not in he_fonts      # no Hebrew letters
    assert "makabiyg-webfont" in en_fonts and "makabiyg-webfont" not in fr_fonts  # no accents
    assert "ankaclm-bold-webfont" in he_fonts and "ankaclm-bold-webfont" not in en_fonts
    assert clock.DEFAULT_FONT in he_fonts & en_fonts & fr_fonts


# ── Settings ──────────────────────────────────────────

def _register(client, username):
    client.cookies.clear()
    client.post("/register", data={"username": username, "password": "correct-horse"})


def test_language_is_saved_and_unsuitable_font_replaced(client):
    _register(client, "languser")
    form = {"location": "Haifa", "calendar": "gregorian"}
    client.post("/config", data={**form, "language": "fr", "font": "Roboto-Bold"})
    cfg = db.get_user_settings("languser")
    assert (cfg["language"], cfg["font"]) == ("fr", "Roboto-Bold")
    # A Hebrew-only font cannot write French: the default font is used instead
    client.post("/config", data={**form, "language": "fr", "font": "ankaclm-bold-webfont"})
    assert db.get_user_settings("languser")["font"] == clock.DEFAULT_FONT
    client.post("/config", data={**form, "language": "xx", "font": "Roboto-Bold"})
    assert db.get_user_settings("languser")["language"] == "he"


def test_clock_image_follows_the_language(client):
    he_img = client.get("/clock.png?blank=0").content
    fr_img = client.get("/clock.png?language=fr").content
    assert fr_img != he_img
    assert client.get("/clock.png?language=xx").status_code == 200


# ── Page language (flags) ─────────────────────────────

def test_page_language_switch(client):
    client.cookies.clear()
    assert "התחבר" in client.get("/").text                    # Hebrew by default
    resp = client.get("/ui-lang/en?next=/", follow_redirects=False)
    assert resp.status_code == 303 and resp.headers["location"] == "/"
    client.cookies.set("ui_lang", "en")
    page = client.get("/").text
    assert 'dir="ltr"' in page and "Log in" in page


def test_page_language_from_browser(client):
    client.cookies.clear()
    assert "Log in" in client.get("/", headers={"Accept-Language": "fr-FR,fr;q=0.9"}).text
    assert "התחבר" in client.get("/", headers={"Accept-Language": "he-IL,he;q=0.9"}).text


def test_page_language_switch_only_returns_to_own_pages(client):
    resp = client.get("/ui-lang/en?next=https://evil.example", follow_redirects=False)
    assert resp.headers["location"] == "/"


def test_settings_page_lists_fonts_of_the_saved_language(client):
    _register(client, "langpage")
    form = {"location": "Haifa", "calendar": "gregorian", "language": "es", "font": "Poppins-Bold"}
    client.post("/config", data=form)
    client.cookies.set("ui_lang", "en")
    page = client.get("/config").text
    assert "My clock settings" in page
    assert 'value="es" selected' in page
    assert 'value="Poppins-Bold" selected' in page
    assert 'value="ankaclm-bold-webfont"' not in page.split("FONTS_BY_LANGUAGE")[0]
