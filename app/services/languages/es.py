"""Spanish: "las siete y cuarto", "las ocho menos diez"."""

_NUMBERS = [
    "", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve",
    "diez", "once", "doce", "trece", "catorce", "quince", "dieciséis", "diecisiete",
    "dieciocho", "diecinueve", "veinte", "veintiuno", "veintidós", "veintitrés",
    "veinticuatro", "veinticinco", "veintiséis", "veintisiete", "veintiocho",
    "veintinueve",
]
_TENS = {3: "treinta", 4: "cuarenta", 5: "cincuenta"}

DAYS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MONTHS = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
          "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
WEATHER = {
    "sun": "Soleado", "sun_cloud": "Poco nuboso", "cloud": "Nublado",
    "cloud_rain": "Lluvia", "cloud_snow": "Nieve", "thunder": "Tormenta",
}

_ONE_LINE_MAX = 18


def _number(n: int) -> str:
    if n < 30:
        return _NUMBERS[n]
    tens, unit = _TENS[n // 10], n % 10
    return tens if unit == 0 else f"{tens} y {_NUMBERS[unit]}"


def _hour(h24: int) -> str:
    n = h24 % 12 or 12
    return "la una" if n == 1 else f"las {_number(n)}"


def period(h24: int) -> str:
    h24 %= 24
    if 1 <= h24 < 6:
        return "de la madrugada"
    if 6 <= h24 < 12:
        return "de la mañana"
    if 12 <= h24 < 20:
        return "de la tarde"
    return "de la noche"


def time_text(h24: int, m: int) -> tuple[list[str], str]:
    hour_of = h24
    if m == 0:
        minutes = "en punto"
    elif m == 15:
        minutes = "y cuarto"
    elif m == 30:
        minutes = "y media"
    elif m > 30 and m % 5 == 0:
        hour_of = h24 + 1
        minutes = "menos cuarto" if m == 45 else f"menos {_number(60 - m)}"
    else:
        minutes = f"y {_number(m)}"
    hour = _hour(hour_of)
    text = f"{hour} {minutes}"
    lines = [text] if len(text) <= _ONE_LINE_MAX else [hour, minutes]
    return lines, period(hour_of)


def day_name(weekday: int) -> str:
    return DAYS[weekday]


def date_text(day: int, month: int) -> str:
    return f"{day} de {MONTHS[month - 1]}"


def jewish_date(hd: int, hm: str, hy: int) -> tuple[str, str]:
    return f"{hd} {hm}", str(hy)


def weather_text(icon_key: str) -> str:
    return WEATHER.get(icon_key, WEATHER["cloud"])


def night_texts() -> tuple[str, str]:
    return "Hora de dormir", "Buenas noches"
