"""Web app. Server-rendered pages, no build step, works on a phone browser."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import os
import threading
import time
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .. import dashboard
from .. import profile as prof
from ..service import cached, open_store, profile_for, recovery_history, scored_day, today_rx
from ..clock import local_today
from . import auth

app = FastAPI(title="Ceiling")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

REFRESH_EVERY_S = 60 * 60
_sync_lock = threading.Lock()


def _signed_in(request: Request) -> bool:
    if os.getenv("RX_OPEN") == "1":   # local development only
        return True
    return auth.enabled() and bool(auth.verify("session", request.cookies.get(auth.COOKIE)))


@app.middleware("http")
async def sign_in_gate(request: Request, call_next):
    """Health data is private. Fails closed: with no accounts configured, nothing opens.
    The home page is the sign-in page until you're signed in."""
    path = request.url.path
    if path in ("/", "/healthz", "/login", "/logout") or path.startswith("/static/") or _signed_in(request):
        return await call_next(request)
    if path.startswith("/api/"):
        return Response("Sign in required.", status_code=401)
    return RedirectResponse("/", status_code=303)


def _login(request: Request, error: bool = False):
    return templates.TemplateResponse(request, "login.html", {"active": "login", "error": error})


@app.post("/login", response_class=HTMLResponse)
async def login_submit(request: Request):
    form = parse_qs((await request.body()).decode())
    email = form.get("email", [""])[0].strip().lower()
    password = form.get("password", [""])[0]
    if not (auth.enabled() and auth.check_password(email, password)):
        return _login(request, error=True)
    r = RedirectResponse("/", status_code=303)
    r.set_cookie(auth.COOKIE, auth.sign("session", email, auth.SESSION_TTL),
                 max_age=auth.SESSION_TTL, httponly=True, samesite="lax",
                 secure=os.getenv("RX_BASE_URL", "").startswith("https://"))
    return r


@app.get("/login")
def login_get():
    return RedirectResponse("/", status_code=303)


@app.get("/logout")
def logout():
    r = RedirectResponse("/", status_code=303)
    r.delete_cookie(auth.COOKIE)
    return r


def _refresh_in_background() -> None:
    """Pull the last few days from intervals.icu at most once an hour, without
    making the page wait. The page shows what's stored; the next load is fresh."""
    key = os.getenv("INTERVALS_API_KEY", "")
    if not key or not _sync_lock.acquire(blocking=False):
        return

    def run():
        store = None
        try:
            from ..sources import intervals
            store = open_store()
            last = float(store.get_meta("last_sync") or 0)
            # Backfill a year until a backfill has actually brought data in; an empty
            # pull (source not ready yet) must not mark history as done or wait an hour.
            backfilled = store.get_meta("backfilled") == "1"
            if backfilled and time.time() - last < REFRESH_EVERY_S:
                return
            days = 3 if backfilled else 365
            store.set_meta("sync_state", f"running since {int(time.time())}")
            client = intervals.IntervalsClient(key, os.getenv("INTERVALS_ATHLETE_ID", "0"))
            intervals.sync(client, store, days=days,
                           log=lambda msg: store.set_meta("sync_progress", str(msg).strip()))
            store.set_meta("last_sync", str(time.time()))
            after = store.counts()
            if after["activities"] or after["wellness"]:
                store.set_meta("backfilled", "1")
                store.set_meta("sync_state", "ok")
            else:
                store.set_meta("sync_state", "ok, but intervals.icu returned no activities or wellness")
                store.set_meta("last_sync", "0")   # try again on the next visit
            store.sync()
            _warm(store)
        except Exception as e:  # never take the site down over a sync
            print(f"sync failed: {e}")
            if store:
                try:
                    store.set_meta("sync_state", f"failed: {type(e).__name__}: {str(e)[:160]}")
                except Exception:
                    pass
        finally:
            _sync_lock.release()

    threading.Thread(target=run, daemon=True).start()


def _warm(store=None) -> None:
    """Build today's page ahead of the first visitor."""
    try:
        store = store or open_store()
        p, rx = today_rx(store)
        cached(store, "dash", rx.date, lambda: dashboard.build(store, p, rx))
    except Exception as e:
        print(f"warm failed: {e}")


@app.on_event("startup")
def _startup() -> None:
    threading.Thread(target=_warm, daemon=True).start()


@app.get("/healthz", response_class=PlainTextResponse)
def healthz():
    """Public, so it shows only plumbing: whether syncing works and how many records
    exist. No dates, names, heart rates or anything else about the athlete."""
    try:
        s = open_store()
        n = s.counts()
        state = s.get_meta("sync_state") or "never run (sign in to start it)"
        last = s.get_meta("last_sync")
        ago = f"{int((time.time() - float(last)) / 60)} min ago" if last else "never"
        return (f"ok\nsync: {state}\nlast complete sync: {ago}\n"
                f"activities: {n['activities']}  with heart-rate streams: {n['streams']}  "
                f"days of sleep/HRV: {n['wellness']}")
    except Exception as e:
        return f"ok\nstore unavailable: {type(e).__name__}"


def _page(request: Request, name: str, **ctx):
    return templates.TemplateResponse(request, name, {"active": name.split(".")[0], **ctx})


@app.get("/", response_class=HTMLResponse)
def today(request: Request, d: str | None = None):
    if not _signed_in(request):
        return _login(request)
    _refresh_in_background()
    store = open_store()
    p, rx = today_rx(store, d)
    dash = cached(store, "dash", rx.date, lambda: dashboard.build(store, p, rx))
    when = date.fromisoformat(rx.date)
    recent = []
    for a in sorted(p.activities, key=lambda a: a.start_local or a.date, reverse=True):
        if a.date >= rx.date:
            continue
        ad = date.fromisoformat(a.date)
        label = "Yesterday" if (when - ad).days == 1 else ad.strftime("%a %b %-d")
        h, m = divmod(round(a.moving_s / 60), 60)
        dur = f"{h}h {m:02d}m" if h else f"{m} min"
        hr = f", {a.avg_hr:.0f} bpm" if a.avg_hr else ""
        recent.append({"when": label, "what": a.name or a.sport.capitalize(), "stats": dur + hr})
        if len(recent) == 5:
            break
    return _page(request, "today.html", rx=rx, d=dash, p=p, recent=recent,
                 spark=dashboard.spark, ceiling_band=dashboard.ceiling_band(rx, p),
                 day_label=when.strftime("%A, %B %-d"))


@app.get("/yesterday", response_class=HTMLResponse)
def yesterday(request: Request, d: str | None = None):
    day = d or (local_today() - timedelta(days=1)).isoformat()
    rx, sessions = scored_day(open_store(), day)
    return _page(request, "yesterday.html", rx=rx, sessions=sessions, day=day)


@app.get("/recovery", response_class=HTMLResponse)
def recovery(request: Request):
    return _page(request, "recovery.html", days=recovery_history(open_store(), 30))


@app.get("/profile", response_class=HTMLResponse)
def profile(request: Request):
    return _page(request, "profile.html", p=profile_for(open_store()))


@app.get("/progress", response_class=HTMLResponse)
def progress(request: Request):
    return _page(request, "progress.html", p=profile_for(open_store()))


@app.get("/api/today")
def api_today(d: str | None = None):
    """JSON for anything else that wants the prescription (a watch face, later)."""
    _, rx = today_rx(open_store(), d)
    return {
        "date": rx.date, "verdict": rx.verdict, "headline": rx.headline, "why": rx.why,
        "do": rx.do, "dont": rx.dont, "tonight": rx.tonight, "sport": rx.sport,
        "hr_low": rx.hr_low, "hr_ceiling": rx.hr_ceiling, "max_minutes": rx.max_minutes,
        "steps": rx.steps, "compress": rx.compress, "stretch": rx.stretch,
        "if_skip": rx.if_skip, "if_overdo": rx.if_overdo,
        "scenarios": rx.scenarios, "notes": rx.notes,
    }
