"""Clock languages: how each language writes the time, dates and other texts.

Every language module exposes the same functions:
  time_text(h24, m)        -> (time lines, period of day)
  day_name(weekday)        -> name of the day (weekday 0 = Monday)
  date_text(day, month)    -> Gregorian date without the year
  jewish_date(hd, hm, hy)  -> (day and month, year) for a Hebrew calendar date
  weather_text(icon_key)   -> short weather description
  night_texts()            -> (title, subtitle) for the night image
"""
import functools
from types import ModuleType

from app.services.languages import en, es, fr, he

LANGUAGES: dict[str, ModuleType] = {"he": he, "en": en, "fr": fr, "es": es}
DEFAULT_LANGUAGE = "he"

# Name of each language in that language, for the settings page
NATIVE_NAMES = {"he": "עברית", "en": "English", "fr": "Français", "es": "Español"}

_WEATHER_ICONS = ("sun", "sun_cloud", "cloud", "cloud_rain", "cloud_snow", "thunder")


def get(code: str | None) -> ModuleType:
    return LANGUAGES.get(code or DEFAULT_LANGUAGE, LANGUAGES[DEFAULT_LANGUAGE])


@functools.lru_cache(maxsize=None)
def all_texts(code: str) -> frozenset[str]:
    """Every text the clock can draw in this language (used to check font coverage)."""
    lang = get(code)
    texts = set()
    for h24 in range(24):
        for m in range(60):
            lines, period = lang.time_text(h24, m)
            texts.update(lines)
            texts.add(period)
    texts.update(lang.day_name(d) for d in range(7))
    texts.update(lang.date_text(day, month) for month in range(1, 13) for day in range(1, 32))
    texts.update(lang.weather_text(icon) for icon in _WEATHER_ICONS)
    texts.update(lang.night_texts())
    return frozenset(texts)
