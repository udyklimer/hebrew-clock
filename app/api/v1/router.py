import re
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
from app import db

router = APIRouter()

DEFAULT_FONT = "DavidLibre-Bold"
DEFAULT_LOCATION = "Haifa"
DEFAULT_CALENDAR = "gregorian"
DEFAULT_SLEEPTIME = "0"
DEFAULT_BLANK = "0"
VALID_CALENDARS = {"gregorian", "jewish"}
MAX_LOCATION_LENGTH = 64

# Absolute path to app/templates relative to app/api/v1/router.py
_TEMPLATES = Jinja2Templates(
    directory=str(Path(__file__).resolve().parents[2] / "templates")
)


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
    
    return _TEMPLATES.TemplateResponse(
        request,
        "index.html",
        {"error": None, "gtag_id": settings.gtag_id}
    )


@router.post("/register", response_class=HTMLResponse, include_in_schema=False)
async def register(
    request: Request,
    username: str = Form(...),
    password: str = Form(...)
) -> Response:
    clean_username = username.strip()
    if not db.is_valid_username(clean_username):
        return _TEMPLATES.TemplateResponse(
            request, "index.html",
            {"error": "Username must contain English letters and digits only.", "gtag_id": settings.gtag_id}
        )
    
    if len(password) < MIN_PASSWORD_LENGTH:
        return _TEMPLATES.TemplateResponse(
            request, "index.html",
            {"error": f"Password must be at least {MIN_PASSWORD_LENGTH} characters.", "gtag_id": settings.gtag_id}
        )

    success = db.register_user(clean_username, password)
    if not success:
        return _TEMPLATES.TemplateResponse(
            request, "index.html",
            {"error": "Username already taken.", "gtag_id": settings.gtag_id}
        )
    
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
    
    return _TEMPLATES.TemplateResponse(
        request, "index.html",
        {"error": "Invalid username or password.", "gtag_id": settings.gtag_id}
    )


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
    
    user_settings = db.get_user_settings(username)
    return _TEMPLATES.TemplateResponse(
        request,
        "config.html",
        {
            "username": username,
            "fonts": sorted(clock.VALID_FONTS),
            "selected_font": user_settings.get("font", DEFAULT_FONT),
            "selected_location": user_settings.get("location", DEFAULT_LOCATION),
            "selected_calendar": user_settings.get("calendar", DEFAULT_CALENDAR),
            "selected_sleeptime": user_settings.get("sleeptime", DEFAULT_SLEEPTIME),
            "selected_sleep_start": user_settings.get("sleep_start", clock.DEFAULT_SLEEP_START),
            "selected_sleep_end": user_settings.get("sleep_end", clock.DEFAULT_SLEEP_END),
            "selected_battery_display": user_settings.get("battery_display", clock.DEFAULT_BATTERY_DISPLAY),
            "selected_battery_position": user_settings.get("battery_position", clock.DEFAULT_BATTERY_POSITION),
            "selected_blank": user_settings.get("blank", DEFAULT_BLANK) == "1",
            "selected_clock_style": user_settings.get("clock_style", clock.DEFAULT_CLOCK_STYLE),
            "saved": request.query_params.get("saved") == "1",
            "gtag_id": settings.gtag_id, 
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
    blank: Optional[str] = Form(None),
    clock_style: str = Form(clock.DEFAULT_CLOCK_STYLE),
    user_session: Optional[str] = Cookie(None, alias=SESSION_COOKIE)
) -> Response:
    username = read_session(user_session)
    if not username:
        return RedirectResponse(url="/", status_code=303)
    
    if font not in clock.VALID_FONTS:
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
    db.update_user_settings(username, font, location, calendar, sleeptime, blank_val, clock_style,
                            sleep_start, sleep_end, battery_display, battery_position)
    return RedirectResponse(url="/config?saved=1", status_code=303)


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
) -> Response:
    # Offer a firmware update when the device reports an older version
    headers = {"Cache-Control": "no-cache"}
    headers.update(await firmware_svc.check_update(fw, request.app.state.http_client))

    # Priority: explicit query params > DB settings for user > fallback defaults
    if user:
        user_cfg = db.get_user_settings(user)
    else:
        user_cfg = {
            "font": DEFAULT_FONT,
            "location": DEFAULT_LOCATION,
            "calendar": DEFAULT_CALENDAR,
            "sleeptime": DEFAULT_SLEEPTIME,
            "blank": DEFAULT_BLANK,
            "clock_style": clock.DEFAULT_CLOCK_STYLE,
        }

    selected_blank = blank if blank is not None else user_cfg.get("blank", DEFAULT_BLANK)

    # Return a blank image if blank mode is enabled
    if selected_blank == "1":
        img_bytes = await run_in_threadpool(clock.generate_blank_image)
        return Response(content=img_bytes, media_type="image/png", headers=headers)

    selected_font = font or user_cfg.get("font", DEFAULT_FONT)
    selected_loc = location or user_cfg.get("location", DEFAULT_LOCATION)
    selected_cal = calendar or user_cfg.get("calendar", DEFAULT_CALENDAR)
    selected_sleep = sleeptime or user_cfg.get("sleeptime", DEFAULT_SLEEPTIME)
    # Sleep mode shows the night image only inside its window. An explicit
    # sleeptime=1 with no window in the request means "now" (older devices
    # decide the time themselves).
    if sleeptime is None:
        sleep_start = user_cfg.get("sleep_start")
        sleep_end = user_cfg.get("sleep_end")
    show_night = selected_sleep == "1" and clock.in_sleep_window(sleep_start, sleep_end)

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

    w = await weather_svc.get_weather(selected_loc, request.app.state.http_client)

    jdate = None
    if selected_cal == "jewish":
        today = clock.get_israel_time().date()
        jdate = await jewish_cal_svc.get_jewish_date(today, request.app.state.http_client)

    img_bytes = await run_in_threadpool(
        clock.generate_clock_image,
        font_name=selected_font,
        sleep_time=show_night,
        weather=w,
        jewish_date=jdate,
        clock_style=selected_style,
        battery=battery,
        charging=charging == "1",
        battery_display=selected_batt_display,
        battery_position=selected_batt_position,
    )
    return Response(
        content=img_bytes,
        media_type="image/png",
        headers=headers,
    )


@router.get("/firmware/{version}.bin", include_in_schema=False)
async def get_firmware(version: str) -> Response:
    """Serves a firmware image cached from the firmware repo's releases."""
    if firmware_svc.parse_version(version) is None or not re.fullmatch(r"[0-9.]+", version):
        return PlainTextResponse("Not found", status_code=404)
    path = firmware_svc.firmware_dir() / f"{version}.bin"
    if not path.exists():
        return PlainTextResponse("Not found", status_code=404)
    return FileResponse(path, media_type="application/octet-stream")