"""Fetch and cache the Jewish (Hebrew) calendar date from hebcal.com."""
import datetime
import httpx
from loguru import logger

_HEBCAL_URL = "https://www.hebcal.com/converter?gd={d}&gm={m}&gy={y}&g2h=1&cfg=json"
_CACHE_TTL_SECONDS = 86400  # date changes at most once per day

_cache: dict[str, dict] = {}


async def get_jewish_date(date: datetime.date, client: httpx.AsyncClient) -> tuple[int, str, int] | None:
    """Returns (day, month name, year) of the Hebrew date, e.g. (27, 'Sivan', 5786).

    The clock's language turns this into text (see app/services/languages).
    """
    key = date.isoformat()
    entry = _cache.get(key, {})
    cached_at = entry.get("time")
    if cached_at and (datetime.datetime.utcnow() - cached_at).total_seconds() < _CACHE_TTL_SECONDS:
        return entry.get("data")

    try:
        url = _HEBCAL_URL.format(d=date.day, m=date.month, y=date.year)
        resp = await client.get(url)
        resp.raise_for_status()
        payload = resp.json()
        result = (int(payload["hd"]), str(payload["hm"]), int(payload["hy"]))
        _cache[key] = {"data": result, "time": datetime.datetime.utcnow()}
        logger.info("hebcal OK [{}]: {}", key, result)
        return result
    except Exception as exc:
        logger.warning("hebcal error [{}]: {}", key, exc)
        return entry.get("data")
