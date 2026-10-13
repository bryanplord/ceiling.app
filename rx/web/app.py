"""Web app. Server-rendered pages, no build step, works on a phone browser."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .. import profile as prof
from ..service import open_store, recovery_history, scored_day, today_rx

app = FastAPI(title="Rx")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _page(request: Request, name: str, **ctx):
    return templates.TemplateResponse(request, name, {"active": name.split(".")[0], **ctx})


@app.get("/", response_class=HTMLResponse)
def today(request: Request, d: str | None = None):
    _, rx = today_rx(open_store(), d)
    return _page(request, "today.html", rx=rx)


@app.get("/yesterday", response_class=HTMLResponse)
def yesterday(request: Request, d: str | None = None):
    day = d or (date.today() - timedelta(days=1)).isoformat()
    rx, sessions = scored_day(open_store(), day)
    return _page(request, "yesterday.html", rx=rx, sessions=sessions, day=day)


@app.get("/recovery", response_class=HTMLResponse)
def recovery(request: Request):
    return _page(request, "recovery.html", days=recovery_history(open_store(), 30))


@app.get("/profile", response_class=HTMLResponse)
def profile(request: Request):
    return _page(request, "profile.html", p=prof.build(open_store()))


@app.get("/api/today")
def api_today(d: str | None = None):
    """JSON for anything else that wants the prescription (a watch face, later)."""
    _, rx = today_rx(open_store(), d)
    return {
        "date": rx.date, "verdict": rx.verdict, "headline": rx.headline, "why": rx.why,
        "do": rx.do, "dont": rx.dont, "tonight": rx.tonight, "sport": rx.sport,
        "hr_low": rx.hr_low, "hr_ceiling": rx.hr_ceiling, "max_minutes": rx.max_minutes,
        "steps": rx.steps, "scenarios": rx.scenarios, "notes": rx.notes,
    }
