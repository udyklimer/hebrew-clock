"""English: "quarter past seven", "twenty to eight"."""

_ONES = ["", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
         "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
         "seventeen", "eighteen", "nineteen"]

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]
WEATHER = {
    "sun": "Sunny", "sun_cloud": "Partly cloudy", "cloud": "Cloudy",
    "cloud_rain": "Rain", "cloud_snow": "Snow", "thunder": "Storm",
}

# A phrase longer than this is split over two lines
_ONE_LINE_MAX = 18


def _number(n: int) -> str:
    if n < 20:
        return _ONES[n]
    tens = {2: "twenty", 3: "thirty", 4: "forty", 5: "fifty"}[n // 10]
    return tens if n % 10 == 0 else f"{tens}-{_ONES[n % 10]}"


def _hour(h24: int) -> str:
    return _number(h24 % 12 or 12)


def _minutes(n: int) -> str:
    if n == 15:
        return "quarter"
    if n == 30:
        return "half"
    if n % 5 == 0:
        return _number(n)
    return "one minute" if n == 1 else f"{_number(n)} minutes"


def period(h24: int) -> str:
    h24 %= 24
    if 5 <= h24 < 12:
        return "in the morning"
    if 12 <= h24 < 17:
        return "in the afternoon"
    if 17 <= h24 < 21:
        return "in the evening"
    return "at night"


def time_text(h24: int, m: int) -> tuple[list[str], str]:
    if m == 0:
        return [f"{_hour(h24)} o'clock"], period(h24)
    if m <= 30:
        first, second, hour_of = _minutes(m), f"past {_hour(h24)}", h24
    else:
        first, second, hour_of = _minutes(60 - m), f"to {_hour(h24 + 1)}", h24 + 1
    text = f"{first} {second}"
    lines = [text] if len(text) <= _ONE_LINE_MAX else [first, second]
    return lines, period(hour_of)


def day_name(weekday: int) -> str:
    return DAYS[weekday]


def date_text(day: int, month: int) -> str:
    return f"{MONTHS[month - 1]} {day}"


def jewish_date(hd: int, hm: str, hy: int) -> tuple[str, str]:
    return f"{hd} {hm}", str(hy)


def weather_text(icon_key: str) -> str:
    return WEATHER.get(icon_key, WEATHER["cloud"])


def night_texts() -> tuple[str, str]:
    return "Time to sleep", "Good night"
