"""French: "sept heures et quart", "huit heures moins dix"."""

_UNITS = ["", "une", "deux", "trois", "quatre", "cinq", "six", "sept", "huit", "neuf"]
_TEENS = ["dix", "onze", "douze", "treize", "quatorze", "quinze", "seize",
          "dix-sept", "dix-huit", "dix-neuf"]
_TENS = {2: "vingt", 3: "trente", 4: "quarante", 5: "cinquante"}

DAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
          "août", "septembre", "octobre", "novembre", "décembre"]
WEATHER = {
    "sun": "Ensoleillé", "sun_cloud": "Éclaircies", "cloud": "Nuageux",
    "cloud_rain": "Pluie", "cloud_snow": "Neige", "thunder": "Orage",
}

_ONE_LINE_MAX = 18

# Every word on the display starts with a capital letter
TITLE_CASE = True


def _number(n: int) -> str:
    """1-59 in words, feminine ('une'), as used for minutes and hours."""
    if n < 10:
        return _UNITS[n]
    if n < 20:
        return _TEENS[n - 10]
    tens, unit = _TENS[n // 10], n % 10
    if unit == 0:
        return tens
    return f"{tens} et une" if unit == 1 else f"{tens}-{_UNITS[unit]}"


def _hour(h24: int) -> str:
    h24 %= 24
    if h24 == 0:
        return "minuit"
    if h24 == 12:
        return "midi"
    n = h24 % 12
    return "une heure" if n == 1 else f"{_number(n)} heures"


def period(h24: int) -> str:
    h24 %= 24
    if h24 in (0, 12):
        return ""  # "midi" and "minuit" already say it
    if h24 < 12:
        return "du matin"
    if h24 < 18:
        return "de l'après-midi"
    return "du soir"


def time_text(h24: int, m: int) -> tuple[list[str], str]:
    hour_of = h24
    if m == 0:
        return [_hour(h24)], period(h24)
    if m == 15:
        minutes = "et quart"
    elif m == 30:
        minutes = "et demi" if h24 % 12 == 0 else "et demie"
    elif m > 30 and m % 5 == 0:
        hour_of = h24 + 1
        minutes = "moins le quart" if m == 45 else f"moins {_number(60 - m)}"
    else:
        minutes = _number(m)
    hour = _hour(hour_of)
    text = f"{hour} {minutes}"
    lines = [text] if len(text) <= _ONE_LINE_MAX else [hour, minutes]
    return lines, period(hour_of)


def day_name(weekday: int) -> str:
    return DAYS[weekday]


def date_text(day: int, month: int) -> str:
    return f"{'1er' if day == 1 else day} {MONTHS[month - 1]}"


def jewish_date(hd: int, hm: str, hy: int) -> tuple[str, str]:
    return f"{hd} {hm}", str(hy)


def weather_text(icon_key: str) -> str:
    return WEATHER.get(icon_key, WEATHER["cloud"])


def night_texts() -> tuple[str, str]:
    return "Au lit !", "Bonne nuit"
