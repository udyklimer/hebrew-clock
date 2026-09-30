from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request, Query, Form, Cookie, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.core.config import settings
from app.services import clock, weather as weather_svc, jewish_cal as jewish_cal_svc
from app.services import seo as seo_svc
from app import db

router = APIRouter()

DEFAULT_FONT = "DavidLibre-Bold"
DEFAULT_LOCATION = "Haifa"
DEFAULT_CALENDAR = "gregorian"
DEFAULT_SLEEPTIME = "0"
DEFAULT_BLANK = "0"

# Absolute path to app/templates relative to app/api/v1/router.py
_TEMPLATES = Jinja2Templates(
    directory=str(Path(__file__).resolve().parents[2] / "templates")
)

@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def home(
    request: Request,
    user_session: Optional[str] = Cookie(None, alias="session_user")
) -> Response:
    if user_session:
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
    
    success = db.register_user(clean_username, password)
    if not success:
        return _TEMPLATES.TemplateResponse(
            request, "index.html",
            {"error": "Username already taken.", "gtag_id": settings.gtag_id}
        )
    
    response = RedirectResponse(url="/config", status_code=303)
    response.set_cookie(key="session_user", value=clean_username.lower(), httponly=True)
    return response


@router.post("/login", response_class=HTMLResponse, include_in_schema=False)
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...)
) -> Response:
    clean_username = username.strip()
    if db.authenticate_user(clean_username, password):
        response = RedirectResponse(url="/config", status_code=303)
        response.set_cookie(key="session_user", value=clean_username.lower(), httponly=True)
        return response
    
    return _TEMPLATES.TemplateResponse(
        request, "index.html",
        {"error": "Invalid username or password.", "gtag_id": settings.gtag_id}
    )


@router.get("/logout", include_in_schema=False)
async def logout():
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie("session_user")
    return response


@router.get("/config", response_class=HTMLResponse, include_in_schema=False)
async def config_page(
    request: Request,
    user_session: Optional[str] = Cookie(None, alias="session_user")
) -> Response:
    if not user_session:
        return RedirectResponse(url="/", status_code=303)
    
    user_settings = db.get_user_settings(user_session)
    return _TEMPLATES.TemplateResponse(
        request,
        "config.html",
        {
            "username": user_session,
            "fonts": sorted(clock.VALID_FONTS),
            "selected_font": user_settings.get("font", DEFAULT_FONT),
            "selected_location": user_settings.get("location", DEFAULT_LOCATION),
            "selected_calendar": user_settings.get("calendar", DEFAULT_CALENDAR),
            "selected_sleeptime": user_settings.get("sleeptime", DEFAULT_SLEEPTIME),
            "selected_blank": user_settings.get("blank", DEFAULT_BLANK) == "1",
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
    blank: Optional[str] = Form(None),
    user_session: Optional[str] = Cookie(None, alias="session_user")
) -> Response:
    if not user_session:
        return RedirectResponse(url="/", status_code=303)
    
    blank_val = "1" if blank == "1" else "0"
    db.update_user_settings(user_session, font, location, calendar, sleeptime, blank_val)
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
    blank: Optional[str] = Query(None),
) -> Response:
    # Priority: DB settings for user > explicit query params > fallback defaults
    if user:
        user_cfg = db.get_user_settings(user)
    else:
        user_cfg = {
            "font": DEFAULT_FONT,
            "location": DEFAULT_LOCATION,
            "calendar": DEFAULT_CALENDAR,
            "sleeptime": DEFAULT_SLEEPTIME,
            "blank": DEFAULT_BLANK,
        }

    selected_blank = blank if blank is not None else user_cfg.get("blank", DEFAULT_BLANK)

    # Return a blank image if blank mode is enabled
    if selected_blank == "1":
        img_bytes = await run_in_threadpool(clock.generate_blank_image)
        return Response(content=img_bytes, media_type="image/png", headers={"Cache-Control": "no-cache"})

    selected_font = font or user_cfg.get("font", DEFAULT_FONT)
    selected_loc = location or user_cfg.get("location", DEFAULT_LOCATION)
    selected_cal = calendar or user_cfg.get("calendar", DEFAULT_CALENDAR)
    selected_sleep = sleeptime or user_cfg.get("sleeptime", DEFAULT_SLEEPTIME)

    w = await weather_svc.get_weather(selected_loc, request.app.state.http_client)

    jdate = None
    if selected_cal == "jewish":
        today = clock.get_israel_time().date()
        jdate = await jewish_cal_svc.get_jewish_date(today, request.app.state.http_client)

    img_bytes = await run_in_threadpool(
        clock.generate_clock_image,
        font_name=selected_font,
        sleep_time=selected_sleep == "1",
        weather=w,
        jewish_date=jdate,
    )
    return Response(
        content=img_bytes,
        media_type="image/png",
        headers={"Cache-Control": "no-cache"},
    )