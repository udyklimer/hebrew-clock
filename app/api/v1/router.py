import re
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request, Query, Form, Cookie, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.core.config import settings
from app.core.security import (
    MIN_PASSWORD_LENGTH,
    SESSION_COOKIE,
    SESSION_MAX_AGE,
    create_session,
    read_session,
)
from app.services import clock, weather as weather_svc, jewish_cal as jewish_cal_svc
from app.services import seo as seo_svc
from app.services import firmware as firmware_svc
from app.services import languages, ui_text
from app import db

router = APIRouter()

DEFAULT_FONT = "DavidLibre-Bold"
DEFAULT_LOCATION = "Haifa"
DEFAULT_CALENDAR = "gregorian"
DEFAULT_SLEEPTIME = "0"
DEFAULT_BLANK = "0"
# How far from the current time a requested minute (at=) may be, in seconds
AT_PAST_LIMIT, AT_FUTURE_LIMIT = 10 * 60, 30 * 60
VALID_CALENDARS = {"gregorian", "jewish"}
MAX_LOCATION_LENGTH = 64

# Absolute path to app/templates relative to app/api/v1/router.py
_TEMPLATES = Jinja2Templates(
    directory=str(Path(__file__).resolve().parents[2] / "templates")
)


def _page(request: Request, template: str, context: dict) -> Response:
    """Renders a page in the visitor's page language."""
    ui_lang = ui_text.ui_language(request)
    return _TEMPLATES.TemplateResponse(request, template, {
        "t": ui_text.TEXT[ui_lang],
        "ui_lang": ui_lang,
        "next_path": request.url.path,
        "gtag_id": settings.gtag_id,
        **context,
    })


def _local_time(moment) -> str:
    """A UTC time as Israel local time, e.g. '21/10/2026 19:14'."""
    return moment.astimezone(clock.ISRAEL_TZ).strftime("%d/%m/%Y %H:%M")


def _login_page(request: Request, error_key: str | None = None, **fmt) -> Response:
    error = ui_text.texts(request)[error_key].format(**fmt) if error_key else None
    return _page(request, "index.html", {"error": error})


@router.get("/ui-lang/{code}", include_in_schema=False)
async def set_ui_language(code: str, next: str = Query("/")) -> Response:
    """Switches the page language (the flags) and returns to the page."""
    response = RedirectResponse(url=next if next in ("/", "/config") else "/", status_code=303)
    if code in ui_text.UI_LANGUAGES:
        response.set_cookie(ui_text.UI_COOKIE, code, max_age=365 * 24 * 3600, samesite="lax")
    return response


def _login_response(request: Request, username: str) -> Response:
    """Redirects to /config with a signed session cookie for the user."""
    response = RedirectResponse(url="/config", status_code=303)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=create_session(username),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
    )
    return response


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def home(
    request: Request,
    user_session: Optional[str] = Cookie(None, alias=SESSION_COOKIE)
) -> Response:
    if read_session(user_session):
        return RedirectResponse(url="/config", status_code=303)
    
    return _login_page(request)


@router.post("/register", response_class=HTMLResponse, include_in_schema=False)
async def register(
    request: Request,
    username: str = Form(...),
    password: str = Form(...)
) -> Response:
    clean_username = username.strip()
    if not db.is_valid_username(clean_username):
        return _login_page(request, "error_username")
    
    if len(password) < MIN_PASSWORD_LENGTH:
        return _login_page(request, "error_password", n=MIN_PASSWORD_LENGTH)

    success = db.register_user(clean_username, password)
    if not success:
        return _login_page(request, "error_taken")
    
    return _login_response(request, clean_username.lower())


@router.post("/login", response_class=HTMLResponse, include_in_schema=False)
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...)
) -> Response:
    clean_username = username.strip()
    if db.authenticate_user(clean_username, password):
        return _login_response(request, clean_username.lower())
    
    return _login_page(request, "error_login")


@router.get("/logout", include_in_schema=False)
async def logout():
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


@router.get("/config", response_class=HTMLResponse, include_in_schema=False)
async def config_page(
    request: Request,
    user_session: Optional[str] = Cookie(None, alias=SESSION_COOKIE)
) -> Response:
    username = read_session(user_session)
    if not username:
        return RedirectResponse(url="/", status_code=303)
    
    t = ui_text.texts(request)

    # Which settings the form edits: the user's (all devices), or one device's.
    # Devices are listed (and editable one by one) only when there are two or more.
    all_devices = db.list_devices(username)
    multi_device = len(all_devices) > 1
    device_id = db.clean_device_id(request.query_params.get("device"))
    selected_device = db.get_device(username, device_id) if device_id and multi_device else None
    user_settings = db.get_effective_settings(
        username, selected_device["device_id"] if selected_device else None)

    # Firmware: what the device last reported, and the latest release
    device_fw, device_seen = db.get_device_firmware(username)
    if device_seen:
        device_seen = _local_time(device_seen)
    release = await firmware_svc.latest_release(request.app.state.http_client)
    latest_fw = release["version"] if release else None

    def is_outdated(version: str | None) -> bool:
        parsed = firmware_svc.parse_version(version)
        return bool(latest_fw and parsed and firmware_svc.parse_version(latest_fw) > parsed)

    update_waiting = is_outdated(device_fw)

    devices = [{
        "id": d["device_id"],
        "name": d["name"] or f"{t['device_default_name']} {d['device_id'][-4:]}",
        "custom_name": d["name"] or "",
        "fw": d["fw"],
        "outdated": is_outdated(d["fw"]),
        "seen": _local_time(d["last_seen"]) if d["last_seen"] else None,
        "battery": clock.battery_percent_from_mv(d["battery_mv"]) if d["battery_mv"] else None,
        "charging": d["charging"],
        "own_settings": d["settings"] is not None,
    } for d in all_devices]
    selected = next((d for d in devices if selected_device and d["id"] == selected_device["device_id"]), None)

    # A single device is not listed: show its version where older firmware's goes
    if len(devices) == 1:
        device_fw, device_seen, update_waiting = devices[0]["fw"], devices[0]["seen"], devices[0]["outdated"]
    if not multi_device:
        devices = []

    language = user_settings.get("language", languages.DEFAULT_LANGUAGE)
    fonts_by_language = {code: clock.fonts_for_language(code) for code in languages.LANGUAGES}
    return _page(
        request,
        "config.html",
        {
            "username": username,
            "clock_languages": list(languages.NATIVE_NAMES.items()),
            "selected_language": language,
            "fonts": fonts_by_language.get(language, []),
            "fonts_by_language": fonts_by_language,
            "font_labels": clock.font_display_names(),
            "default_font": DEFAULT_FONT,
            "selected_font": user_settings.get("font", DEFAULT_FONT),
            "selected_location": user_settings.get("location", DEFAULT_LOCATION),
            "selected_calendar": user_settings.get("calendar", DEFAULT_CALENDAR),
            "selected_sleeptime": user_settings.get("sleeptime", DEFAULT_SLEEPTIME),
            "selected_sleep_start": user_settings.get("sleep_start", clock.DEFAULT_SLEEP_START),
            "selected_sleep_end": user_settings.get("sleep_end", clock.DEFAULT_SLEEP_END),
            "selected_battery_display": user_settings.get("battery_display", clock.DEFAULT_BATTERY_DISPLAY),
            "selected_battery_position": user_settings.get("battery_position", clock.DEFAULT_BATTERY_POSITION),
            "selected_batch_minutes": user_settings.get("batch_minutes", clock.DEFAULT_BATCH_MINUTES),
            "min_batch_minutes": clock.MIN_BATCH_MINUTES,
            "max_batch_minutes": clock.MAX_BATCH_MINUTES,
            "selected_blank": user_settings.get("blank", DEFAULT_BLANK) == "1",
            "selected_clock_style": user_settings.get("clock_style", clock.DEFAULT_CLOCK_STYLE),
            "selected_grayscale": user_settings.get("grayscale", "0") == "1",
            "selected_bottom_bar": user_settings.get("bottom_bar", "1") == "1",
            "saved": request.query_params.get("saved") == "1",
            "device_fw": device_fw,
            "device_seen": device_seen,
            "latest_fw": latest_fw,
            "update_waiting": update_waiting,
            "devices": devices,
            "selected_device": selected,
        }
    )


@router.post("/config", include_in_schema=False)
async def save_config(
    font: str = Form(...),
    location: str = Form(...),
    calendar: str = Form(...),
    sleeptime: str = Form("0"),
    sleep_start: str = Form(clock.DEFAULT_SLEEP_START),
    sleep_end: str = Form(clock.DEFAULT_SLEEP_END),
    battery_display: str = Form(clock.DEFAULT_BATTERY_DISPLAY),
    battery_position: str = Form(clock.DEFAULT_BATTERY_POSITION),
    batch_minutes: int = Form(clock.DEFAULT_BATCH_MINUTES),
    blank: Optional[str] = Form(None),
    clock_style: str = Form(clock.DEFAULT_CLOCK_STYLE),
    language: str = Form(languages.DEFAULT_LANGUAGE),
    grayscale: str = Form("0"),
    bottom_bar: str = Form("1"),
    device: Optional[str] = Form(None),
    own_settings: Optional[str] = Form(None),
    user_session: Optional[str] = Cookie(None, alias=SESSION_COOKIE)
) -> Response:
    username = read_session(user_session)
    if not username:
        return RedirectResponse(url="/", status_code=303)

    if language not in languages.LANGUAGES:
        language = languages.DEFAULT_LANGUAGE
    if font not in clock.fonts_for_language(language):
        font = DEFAULT_FONT
    if calendar not in VALID_CALENDARS:
        calendar = DEFAULT_CALENDAR
    location = location.strip()[:MAX_LOCATION_LENGTH] or DEFAULT_LOCATION
    sleeptime = "1" if sleeptime == "1" else "0"
    sleep_start = sleep_start.strip()
    sleep_end = sleep_end.strip()
    if clock.parse_hhmm(sleep_start) is None:
        sleep_start = clock.DEFAULT_SLEEP_START
    if clock.parse_hhmm(sleep_end) is None:
        sleep_end = clock.DEFAULT_SLEEP_END
    blank_val = "1" if blank == "1" else "0"
    if clock_style not in clock.VALID_CLOCK_STYLES:
        clock_style = clock.DEFAULT_CLOCK_STYLE
    if battery_display not in clock.VALID_BATTERY_DISPLAYS:
        battery_display = clock.DEFAULT_BATTERY_DISPLAY
    if battery_position not in clock.VALID_BATTERY_POSITIONS:
        battery_position = clock.DEFAULT_BATTERY_POSITION
    values = {
        "font": font, "location": location, "calendar": calendar, "sleeptime": sleeptime,
        "blank": blank_val, "clock_style": clock_style,
        "sleep_start": sleep_start, "sleep_end": sleep_end,
        "battery_display": battery_display, "battery_position": battery_position,
        "batch_minutes": min(clock.MAX_BATCH_MINUTES, max(clock.MIN_BATCH_MINUTES, batch_minutes)),
        "language": language,
        "grayscale": "1" if grayscale == "1" else "0",
        "bottom_bar": "0" if bottom_bar == "0" else "1",
    }

    # One device: its own settings, or back to the user's
    device_id = db.clean_device_id(device)
    if device_id and db.get_device(username, device_id) and len(db.list_devices(username)) > 1:
        db.set_device_settings(username, device_id, values if own_settings == "1" else None)
        return RedirectResponse(url=f"/config?device={device_id}&saved=1", status_code=303)

    db.update_user_settings(username, **values)
    return RedirectResponse(url="/config?saved=1", status_code=303)


@router.post("/devices/{device_id}/rename", include_in_schema=False)
async def rename_device(
    device_id: str,
    name: str = Form(""),
    user_session: Optional[str] = Cookie(None, alias=SESSION_COOKIE),
) -> Response:
    username = read_session(user_session)
    if not username:
        return RedirectResponse(url="/", status_code=303)
    device_id = db.clean_device_id(device_id)
    if device_id:
        db.rename_device(username, device_id, name)
    return RedirectResponse(url="/config", status_code=303)


@router.post("/devices/{device_id}/delete", include_in_schema=False)
async def delete_device(
    device_id: str,
    user_session: Optional[str] = Cookie(None, alias=SESSION_COOKIE),
) -> Response:
    username = read_session(user_session)
    if not username:
        return RedirectResponse(url="/", status_code=303)
    device_id = db.clean_device_id(device_id)
    if device_id:
        db.delete_device(username, device_id)
    return RedirectResponse(url="/config", status_code=303)


@router.get("/robots.txt", response_class=PlainTextResponse, include_in_schema=False)
async def robots_txt(request: Request) -> PlainTextResponse:
    base_url = str(request.base_url).rstrip("/")
    return PlainTextResponse(seo_svc.generate_robots(base_url))


@router.get("/sitemap.xml", include_in_schema=False)
async def sitemap_xml(request: Request) -> Response:
    base_url = str(request.base_url).rstrip("/")
    return Response(
        content=seo_svc.generate_sitemap(base_url),
        media_type="application/xml",
    )


@router.get("/clock.png", response_class=Response)
@router.get("/clock", response_class=Response, include_in_schema=False)
async def get_clock(
    request: Request,
    user: Optional[str] = Query(None),
    font: Optional[str] = Query(None),
    location: Optional[str] = Query(None),
    calendar: Optional[str] = Query(None),
    sleeptime: Optional[str] = Query(None),
    sleep_start: Optional[str] = Query(None),
    sleep_end: Optional[str] = Query(None),
    battery: Optional[int] = Query(None, ge=0, le=100, description="Battery charge in percent"),
    battery_mv: Optional[int] = Query(None, ge=0, le=10000, description="Battery voltage in millivolts"),
    charging: Optional[str] = Query(None),
    battery_display: Optional[str] = Query(None),
    battery_position: Optional[str] = Query(None),
    blank: Optional[str] = Query(None),
    clock_style: Optional[str] = Query(None),
    fw: Optional[str] = Query(None, description="Firmware version of the device, e.g. 1.4.0"),
    at: Optional[int] = Query(None, description="Unix time of the minute to render (for batch fetching)"),
    language: Optional[str] = Query(None, description="Clock language: he, en, fr or es"),
    device: Optional[str] = Query(None, description="Device ID, e.g. the chip's MAC address"),
    gray: Optional[str] = Query(None, description="1 for 4 shades of gray, 0 for black and white"),
    bottom_bar: Optional[str] = Query(None, description="0 hides the bottom row (date, period, weather)"),
) -> Response:
    # The minute to render: a requested one (battery save mode fetches several
    # minutes ahead), or now plus the display lag
    if at is not None:
        offset = at - time.time()
        if not -AT_PAST_LIMIT <= offset <= AT_FUTURE_LIMIT:
            return PlainTextResponse("at is too far from the current time", status_code=422)
        now = clock.israel_time_at(at)
    else:
        now = clock.get_israel_time()

    # Offer a firmware update when the device reports an older version
    headers = {"Cache-Control": "no-cache"}
    headers.update(await firmware_svc.check_update(fw, request.app.state.http_client))

    # Priority: explicit query params > the device's own settings > the user's
    # settings > fallback defaults
    if user:
        device_id = db.clean_device_id(device)
        if device_id:
            db.record_device(user, device_id, fw, battery_mv,
                             None if charging is None else charging == "1")
        user_cfg = db.get_effective_settings(user, device_id)
        if fw:
            db.record_device_firmware(user, fw)
    else:
        user_cfg = {
            "font": DEFAULT_FONT,
            "location": DEFAULT_LOCATION,
            "calendar": DEFAULT_CALENDAR,
            "sleeptime": DEFAULT_SLEEPTIME,
            "blank": DEFAULT_BLANK,
            "clock_style": clock.DEFAULT_CLOCK_STYLE,
        }
    # Battery save mode: how many minutes of images the device should fetch at once
    headers["X-Batch-Minutes"] = str(user_cfg.get("batch_minutes", clock.DEFAULT_BATCH_MINUTES))
    # 4 shades of gray or black and white; the header tells the device which it got
    use_gray = (gray if gray is not None else user_cfg.get("grayscale", "0")) == "1"
    headers["X-Gray-Levels"] = "4" if use_gray else "2"

    selected_blank = blank if blank is not None else user_cfg.get("blank", DEFAULT_BLANK)

    # Return a blank image if blank mode is enabled
    if selected_blank == "1":
        img_bytes = await run_in_threadpool(clock.generate_blank_image)
        return Response(content=img_bytes, media_type="image/png", headers=headers)

    selected_lang = language or user_cfg.get("language", languages.DEFAULT_LANGUAGE)
    if selected_lang not in languages.LANGUAGES:
        selected_lang = languages.DEFAULT_LANGUAGE
    selected_font = font or user_cfg.get("font", DEFAULT_FONT)
    if selected_font not in clock.fonts_for_language(selected_lang):
        selected_font = DEFAULT_FONT
    selected_loc = location or user_cfg.get("location", DEFAULT_LOCATION)
    selected_cal = calendar or user_cfg.get("calendar", DEFAULT_CALENDAR)
    selected_sleep = sleeptime or user_cfg.get("sleeptime", DEFAULT_SLEEPTIME)
    # Sleep mode shows the night image only inside its window. An explicit
    # sleeptime=1 with no window in the request means "now" (older devices
    # decide the time themselves).
    if sleeptime is None:
        sleep_start = user_cfg.get("sleep_start")
        sleep_end = user_cfg.get("sleep_end")
    show_night = selected_sleep == "1" and clock.in_sleep_window(sleep_start, sleep_end, now)

    # Battery: the device reports a percentage, or a voltage that is converted here
    if battery is None and battery_mv is not None:
        battery = clock.battery_percent_from_mv(battery_mv)
    selected_batt_display = battery_display or user_cfg.get("battery_display", clock.DEFAULT_BATTERY_DISPLAY)
    if selected_batt_display not in clock.VALID_BATTERY_DISPLAYS:
        selected_batt_display = clock.DEFAULT_BATTERY_DISPLAY
    selected_batt_position = battery_position or user_cfg.get("battery_position", clock.DEFAULT_BATTERY_POSITION)
    if selected_batt_position not in clock.VALID_BATTERY_POSITIONS:
        selected_batt_position = clock.DEFAULT_BATTERY_POSITION
    selected_style = clock_style or user_cfg.get("clock_style", clock.DEFAULT_CLOCK_STYLE)
    if selected_style not in clock.VALID_CLOCK_STYLES:
        selected_style = clock.DEFAULT_CLOCK_STYLE

    # The bottom row shows the date and weather; without it neither is fetched
    show_bottom = (bottom_bar if bottom_bar is not None else user_cfg.get("bottom_bar", "1")) != "0"
    w = await weather_svc.get_weather(selected_loc, request.app.state.http_client) if show_bottom else None

    jdate = None
    if selected_cal == "jewish" and show_bottom:
        today = now.date()
        jdate = await jewish_cal_svc.get_jewish_date(today, request.app.state.http_client)

    img_bytes = await run_in_threadpool(
        clock.generate_clock_image,
        font_name=selected_font,
        sleep_time=show_night,
        weather=w,
        jewish_parts=jdate,
        clock_style=selected_style,
        language=selected_lang,
        battery=battery,
        charging=charging == "1",
        battery_display=selected_batt_display,
        battery_position=selected_batt_position,
        now=now,
        gray=use_gray,
        bottom_bar=show_bottom,
    )
    return Response(
        content=img_bytes,
        media_type="image/png",
        headers=headers,
    )


_INSTALLER_UNAVAILABLE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Install Hebrew Clock</title></head>
<body style="font: 17px/1.5 system-ui, sans-serif; max-width: 560px; margin: 0 auto; padding: 32px 16px;">
<h1>Install Hebrew Clock</h1>
<p>The installer isn't available right now: there is no firmware release with an installable image yet,
or it could not be downloaded. Please try again later.</p>
<p dir="rtl" lang="he">ההתקנה אינה זמינה כרגע: עדיין אין גרסת קושחה שאפשר להתקין מכאן, או שלא ניתן היה להוריד אותה. נסו שוב מאוחר יותר.</p>
</body></html>"""


@router.get("/install", include_in_schema=False)
@router.get("/install/", include_in_schema=False)
async def install_page(request: Request) -> Response:
    """Web installer for new devices (the page from the firmware repo's install/ folder)."""
    files = await firmware_svc.installer_files(request.app.state.http_client)
    if not files:
        return HTMLResponse(_INSTALLER_UNAVAILABLE, status_code=503)
    return FileResponse(files["page"], media_type="text/html; charset=utf-8",
                        headers={"Cache-Control": "no-cache"})


@router.get("/install/manifest.json", include_in_schema=False)
async def install_manifest(request: Request) -> Response:
    files = await firmware_svc.installer_files(request.app.state.http_client)
    if not files:
        return PlainTextResponse("Not available", status_code=503)
    return FileResponse(files["manifest"], media_type="application/json",
                        headers={"Cache-Control": "no-cache"})


@router.get("/install/firmware-full.bin", include_in_schema=False)
async def install_firmware(request: Request) -> Response:
    files = await firmware_svc.installer_files(request.app.state.http_client)
    if not files:
        return PlainTextResponse("Not available", status_code=503)
    return FileResponse(files["full"], media_type="application/octet-stream",
                        headers={"Cache-Control": "no-cache"})


@router.get("/firmware/{version}.bin", include_in_schema=False)
async def get_firmware(version: str) -> Response:
    """Serves a firmware image cached from the firmware repo's releases."""
    if firmware_svc.parse_version(version) is None or not re.fullmatch(r"[0-9.]+", version):
        return PlainTextResponse("Not found", status_code=404)
    path = firmware_svc.firmware_dir() / f"{version}.bin"
    if not path.exists():
        return PlainTextResponse("Not found", status_code=404)
    return FileResponse(path, media_type="application/octet-stream")