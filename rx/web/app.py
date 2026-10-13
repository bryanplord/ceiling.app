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

from .. import dashboard, story as storymod
from ..analysis import sleep as sleepmod
from ..analysis import distribution as dist
from .. import profile as prof
from ..service import cached, peek, plan_history, plan_with_changes, open_store, profile_for, recovery_history, scored_day, today_rx
from ..clock import local_today
from . import auth

app = FastAPI(title="Ceiling")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
from ..analysis.hr import ZONE_LABEL as _ZL
templates.env.globals["zone_label"] = _ZL
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")

REFRESH_EVERY_S = 60 * 60
MORNING_EVERY_S = 10 * 60


def _have_last_night(store) -> bool:
    today = local_today().isoformat()
    w = next((w for w in store.wellness() if w.date == today), None)
    return bool(w and (w.hrv or w.sleep_secs or w.sleep_score))
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
    if path in ("/", "/healthz", "/login", "/logout", "/plan.ics") or path.startswith("/static/") or _signed_in(request):
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


def _refresh_in_background(force: bool = False) -> None:
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
            # Mornings: until last night's sleep/HRV has landed, check every 10 minutes.
            # Garmin only uploads it once the watch syncs to the phone.
            every = REFRESH_EVERY_S if _have_last_night(store) else MORNING_EVERY_S
            if backfilled and not force and time.time() - last < every:
                return
            days = 3 if backfilled else 365
            store.set_meta("sync_state", f"running since {int(time.time())}")
            client = intervals.IntervalsClient(key, os.getenv("INTERVALS_ATHLETE_ID", "0"))
            intervals.sync(client, store, days=days,
                           log=lambda msg: store.set_meta("sync_progress", str(msg).strip()))
            try:
                intervals.backfill_power(client, store, log=lambda msg: store.set_meta("sync_progress", str(msg).strip()))
            except Exception as e:
                print(f"power backfill failed: {e}")
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
        cached(store, "story", rx.date, lambda: storymod.build(store, p))
        cached(store, "planx", rx.date, lambda: plan_with_changes(store))
        cached(store, "planhist", rx.date, lambda: plan_history(store))
        from .. import workouts as wk
        cached(store, "workouts", rx.date, lambda: wk.build(store))
    except Exception as e:
        print(f"warm failed: {e}")


@app.on_event("startup")
def _startup() -> None:
    threading.Thread(target=_warm, daemon=True).start()


@app.post("/sync")
def sync_now():
    """The Sync now button. Signed-in only (the gate covers it). Pulls immediately,
    ignoring the hourly limit; the page refreshes itself until it's done."""
    _refresh_in_background(force=True)
    return RedirectResponse("/?syncing=1", status_code=303)


def _resp_keys(store) -> str:
    import json as _json
    keys = set()
    for r in store.wellness_raw((local_today() - timedelta(days=10)).isoformat()):
        try:
            d = _json.loads(r["raw"]) if r["raw"] else {}
        except Exception:
            continue
        keys |= {k for k, v in d.items() if v not in (None, "", [], {})}
    return ", ".join(sorted(keys)) or "none"


@app.get("/healthz", response_class=PlainTextResponse)
def healthz():
    """Public, so it shows only plumbing: whether syncing works and how many records
    exist. No dates, names, heart rates or anything else about the athlete.
    The keep-awake ping hits this every 10 minutes, so it also drives the refresh:
    the morning's data is pulled before you open the page."""
    _refresh_in_background()
    try:
        s = open_store()
        n = s.counts()
        state = s.get_meta("sync_state") or "never run (sign in to start it)"
        last = s.get_meta("last_sync")
        ago = f"{int((time.time() - float(last)) / 60)} min ago" if last else "never"
        ver = (os.getenv("RENDER_GIT_COMMIT") or "local")[:7]
        return (f"ok\nversion: {ver}\nsync: {state}\nlast complete sync: {ago}\n"
                f"activities: {n['activities']}  with heart-rate streams: {n['streams']}  "
                f"days of sleep/HRV: {n['wellness']}\n"
                f"nights with breathing rate (last 30): "
                f"{sum(1 for w in s.wellness((local_today() - timedelta(days=30)).isoformat()) if w.breathing)}")
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
    st = cached(store, "story", rx.date, lambda: storymod.build(store, p))
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
    last = store.get_meta("last_sync")
    mins_ago = int((time.time() - float(last)) / 60) if last and float(last) > 0 else None
    syncing = _sync_lock.locked()   # only a sync actually in progress; never a stale flag
    return _page(request, "today.html", rx=rx, waiting=not _have_last_night(store), updated=mins_ago,
                 syncing=syncing, d=dash, p=p, recent=recent,
                 spark=dashboard.spark, room=dashboard.room(rx, p, *cached(store, "hrhist", rx.date, lambda: dist.hr_histogram(store, p))),
                 st=st, hist=storymod.history_chart(st),
                 sleep_chart=sleepmod.chart(rx.sleep) if rx.sleep else "",
                 base_need=sleepmod._hm(rx.sleep.base) if rx.sleep else "",
                 day_label=when.strftime("%A, %B %-d"))


@app.get("/calendar/history", response_class=HTMLResponse)
def history_page(request: Request, m: str | None = None):
    from .. import history as histmod
    store = open_store()
    p, rx = today_rx(store)
    ym = m if (m and len(m) == 7) else rx.date[:7]
    def live():
        import json as _json
        out = {}
        y_, m_ = map(int, ym.split("-"))
        for dd in range(1, 32):
            k = f"{ym}-{dd:02d}"
            v = store.get_meta(f"rx:{k}")
            if v:
                out[k] = tuple(_json.loads(v))
        return out
    mon = cached(store, f"month:{ym}", rx.date, lambda: histmod.month(p, ym, rx.date, live()))
    return _page(request, "history.html", mon=mon, active="calendar")


@app.get("/calendar", response_class=HTMLResponse)
def plan_page(request: Request):
    from ..prescribe import plan as planmod
    store = open_store()
    p, rx = today_rx(store)
    pl, changed = cached(store, "planx", rx.date, lambda: plan_with_changes(store))
    hist = peek(store, "planhist", rx.date)
    if hist is None:
        threading.Thread(target=lambda: cached(store, "planhist", rx.date, lambda: plan_history(store)), daemon=True).start()
    email = auth.verify("session", request.cookies.get(auth.COOKIE)) if auth.enabled() else None
    feed = None
    if email:
        tok = auth.sign("cal", email, 10 * 365 * 24 * 3600)
        feed = f"{request.url.netloc}/plan.ics?k={tok}"
    return _page(request, "calendar.html", plan=pl, changed=changed, hist=hist, feed=feed, date=date)


@app.get("/plan.ics")
def plan_ics(k: str = ""):
    """Calendar subscription. Calendar apps can't sign in, so the link carries a long-lived signed key."""
    from ..prescribe import plan as planmod
    if not (os.getenv("RX_OPEN") == "1" or (auth.enabled() and auth.verify("cal", k))):
        return Response("Not found.", status_code=404)
    store = open_store()
    p, rx = today_rx(store)
    pl, _ = cached(store, "planx", rx.date, lambda: plan_with_changes(store))
    return Response(planmod.ics(pl), media_type="text/calendar; charset=utf-8",
                    headers={"Cache-Control": "no-cache"})


@app.get("/zones", response_class=HTMLResponse)
def zones(request: Request):
    from ..analysis import distribution as dist
    store = open_store()
    p, rx = today_rx(store)
    hr = cached(store, "hrdist", rx.date, lambda: dist.hr_distribution(store, p))
    pw = cached(store, "pwdist", rx.date, lambda: dist.power_distribution(store, p))
    return _page(request, "zones.html", hr=hr, pw=pw, trend=dist.trend_chart)


@app.get("/you", response_class=HTMLResponse)
def you_page(request: Request):
    from .. import benchmarks as bm
    store = open_store()
    p, rx = today_rx(store)
    rows, est, vo2, vr = cached(store, "bench", rx.date, lambda: bm.build(store, p))
    return _page(request, "you.html", rows=rows, est=est, vo2=vo2, scale=bm.scale, age=bm.AGE)


@app.get("/data", response_class=HTMLResponse)
def data_page(request: Request):
    """What Ceiling actually received, day by day. For checking a missing night."""
    import json as _json
    store = open_store()
    since = (local_today() - timedelta(days=9)).isoformat()
    rows = []
    for r in store.wellness_raw(since):
        raw = {}
        try:
            raw = _json.loads(r["raw"]) if r["raw"] else {}
        except ValueError:
            pass
        rows.append({"date": r["date"], "source": r["source"], "hrv": r["hrv"], "rhr": r["rhr"],
                     "score": r["sleep_score"],
                     "sleep": (f"{int(r['sleep_secs'] // 3600)}h {int(r['sleep_secs'] % 3600 // 60):02d}m"
                               if r["sleep_secs"] else None),
                     "updated": raw.get("updated"),
                     "fields": ", ".join(sorted(k for k, v in raw.items() if v not in (None, "", [], 0)))})
    have = {r["date"] for r in rows}
    days = [(local_today() - timedelta(days=i)).isoformat() for i in range(10)]
    last = store.get_meta("last_sync")
    return _page(request, "data.html", rows=rows, missing=[d for d in days if d not in have],
                 today=local_today().isoformat(), state=store.get_meta("sync_state") or "",
                 progress=store.get_meta("sync_progress") or "",
                 last=int((time.time() - float(last)) / 60) if last and float(last) > 0 else None)


@app.get("/trends", response_class=HTMLResponse)
def trends_page(request: Request):
    from .. import trends as tr
    store = open_store()
    p, rx = today_rx(store)
    t = cached(store, "trends", rx.date, lambda: tr.build(p))
    return _page(request, "trends.html", t=t, axis=tr.axis(t))


@app.get("/workouts", response_class=HTMLResponse)
def workouts_page(request: Request):
    from .. import workouts as wk
    store = open_store()
    p, rx = today_rx(store)
    rows = peek(store, "workouts", rx.date)
    if rows is None:
        threading.Thread(target=lambda: cached(store, "workouts", rx.date, lambda: wk.build(store)), daemon=True).start()
        return _page(request, "workouts.html", rows=None, date=date)
    agg = wk.aggregate(rows)
    easy = sum(x[3] for x in agg[:3])
    hard = sum(x[3] for x in agg[4:])
    mid = agg[3][3]
    if hard > 22:
        note = (f"{hard:.0f}% of your time is Threshold or Max, about twice what most strong endurance athletes do. "
                f"Too much hard work is what keeps HRV down; trade some of it for easy time.")
    elif mid > 15:
        note = (f"{mid:.0f}% of your time is Tempo, the zone that's too hard to recover from quickly and too easy "
                f"to build top-end fitness. Make easy days easier and hard days harder.")
    elif hard < 8:
        note = f"Only {hard:.0f}% of your time is Threshold or Max. One or two hard sessions a week would move your fitness more."
    elif easy < 70:
        note = f"Only {easy:.0f}% of your time is easy. Most of your training should be easy so the hard days can be hard."
    else:
        note = "A good balance: mostly easy, with a real slice of hard."
    scale = max([x[3] for x in agg] + [x[4] for x in agg]) * 1.1
    return _page(request, "workouts.html", rows=rows, agg=agg, aggnote=note, scale=scale, date=date)


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
