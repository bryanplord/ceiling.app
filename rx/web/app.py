"""Web app. Server-rendered pages, no build step, works on a phone browser."""
from __future__ import annotations

from datetime import date, datetime, timedelta
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
from ..service import bust, today_after, current_owner, owner_of, cached, peek, plan_history, plan_with_changes, open_store, profile_for, recovery_history, scored_day, today_rx
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
    w = next((w for w in store.wellness(today) if w.date == today), None)
    return bool(w and (w.hrv or w.sleep_secs or w.sleep_score))


def _signed_in(request: Request) -> bool:
    if os.getenv("RX_OPEN") == "1":   # local development only
        return True
    return auth.enabled() and bool(auth.verify("session", request.cookies.get(auth.COOKIE)))


@app.exception_handler(Exception)
async def _crash(request: Request, exc: Exception):
    """Never a bare 500. The traceback goes to the log and to the signed-in athlete's own Data page,
    so a crash someone else hits can be read without access to the server."""
    import traceback
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[-6000:]
    print(f"500 on {request.url.path}:\n{tb}")
    try:
        import json as _json
        open_store().set_meta("last_error", _json.dumps({"at": time.strftime("%Y-%m-%d %H:%M:%S"),
                                                         "path": request.url.path, "tb": tb}))
    except Exception:
        pass
    return HTMLResponse("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
                        "<title>Ceiling</title><body style=\"font:17px/1.5 -apple-system,Arial;max-width:560px;margin:15vh auto;padding:0 20px\">"
                        "<h1 style='font-size:28px'>Something broke on this page.</h1>"
                        "<p>It's been recorded. Try again in a minute, or go to <a href='/data'>Data</a> to see what happened.</p>",
                        status_code=500)


@app.middleware("http")
async def sign_in_gate(request: Request, call_next):
    """Health data is private. Fails closed: with no accounts configured, nothing opens.
    The home page is the sign-in page until you're signed in."""
    path = request.url.path
    email = auth.verify("session", request.cookies.get(auth.COOKIE)) if auth.enabled() else None
    current_owner.set(owner_of(email))
    if path.startswith("/static/"):
        # Icons and the share image: versioned links (?v=) never change, the rest rarely do.
        r = await call_next(request)
        if r.status_code == 200:
            r.headers["Cache-Control"] = ("public, max-age=31536000, immutable" if "v" in request.query_params
                                          else "public, max-age=86400")
        return r
    if path in ("/favicon.ico", "/apple-touch-icon.png", "/apple-touch-icon-precomposed.png", "/healthz", "/login", "/logout", "/plan.ics", "/how", "/science", "/join", "/morning", "/after", "/schedule", "/learns"):
        return await call_next(request)
    if _signed_in(request):
        # A new athlete has no data yet: every page sends them to Data to connect it.
        o = current_owner.get()
        if o and path not in ("/data", "/connect", "/sync", "/logout") and not path.startswith("/coach"):
            c = open_store(o).counts()
            if not (c["activities"] or c["wellness"]):
                return RedirectResponse("/data", status_code=303)
        return await call_next(request)
    if path == "/":
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
def login_get(request: Request):
    if _signed_in(request):
        return RedirectResponse("/", status_code=303)
    return _login(request)


def _site(request: Request, name: str, **ctx):
    return templates.TemplateResponse(request, name, {"active": name[5:].split(".")[0], **ctx})


@app.get("/favicon.ico")
def favicon():
    from fastapi.responses import FileResponse
    return FileResponse(Path(__file__).parent / "static" / "favicon.ico", media_type="image/x-icon",
                        headers={"Cache-Control": "public, max-age=86400"})


@app.get("/apple-touch-icon.png")
@app.get("/apple-touch-icon-precomposed.png")
def touch_icon():
    from fastapi.responses import FileResponse
    return FileResponse(Path(__file__).parent / "static" / "apple-touch-icon.png", media_type="image/png")


@app.get("/how", response_class=HTMLResponse)
def site_how(request: Request):
    return _site(request, "site_how.html")


@app.get("/science", response_class=HTMLResponse)
def site_science(request: Request):
    return _site(request, "site_science.html")


@app.get("/morning", response_class=HTMLResponse)
def site_morning(request: Request):
    return _site(request, "site_morning.html")


@app.get("/after", response_class=HTMLResponse)
def site_after(request: Request):
    return _site(request, "site_after.html")


@app.get("/schedule", response_class=HTMLResponse)
def site_schedule(request: Request):
    return _site(request, "site_schedule.html")


@app.get("/learns", response_class=HTMLResponse)
def site_learns(request: Request):
    return _site(request, "site_learns.html")


@app.get("/join", response_class=HTMLResponse)
def site_join(request: Request, done: int = 0):
    return _site(request, "site_join.html", done=bool(done))


@app.post("/join")
async def site_join_post(request: Request):
    """The waitlist: kept in the primary database's meta table, one row per email."""
    import json as _json
    form = parse_qs((await request.body()).decode())
    email = (form.get("email", [""])[0] or "").strip().lower()[:200]
    if "@" in email and "." in email.split("@")[-1]:
        open_store("").set_meta(f"waitlist:{email}", _json.dumps({
            "device": (form.get("device", [""])[0] or "")[:40], "hours": (form.get("hours", [""])[0] or "")[:40],
            "at": int(time.time())}))
    return RedirectResponse("/join?done=1", status_code=303)


@app.get("/logout")
def logout():
    r = RedirectResponse("/", status_code=303)
    r.delete_cookie(auth.COOKIE)
    return r


_sync_locks: dict = {}


def _creds(owner: str) -> tuple[str, str]:
    """intervals.icu key and athlete id: the primary athlete's from the environment,
    everyone else's from what they entered on their Data page (kept in their own tables)."""
    if not owner:
        return os.getenv("INTERVALS_API_KEY", ""), os.getenv("INTERVALS_ATHLETE_ID", "0")
    st = open_store(owner)
    return auth.unseal(st.get_meta("intervals_key") or ""), st.get_meta("intervals_athlete") or "0"


def _all_owners() -> list[str]:
    return sorted({owner_of(e) for e in auth.users()} | {""})


def _refresh_all(force: bool = False) -> None:
    for o in _all_owners():
        _refresh_in_background(force, o)


def _refresh_in_background(force: bool = False, owner: str | None = None) -> None:
    """Pull the last few days from intervals.icu at most once an hour, without
    making the page wait. The page shows what's stored; the next load is fresh."""
    owner = current_owner.get() if owner is None else owner
    key, athlete = _creds(owner)
    lock = _sync_locks.setdefault(owner, threading.Lock())
    if not key or not lock.acquire(blocking=False):
        return

    def run():
        store = None
        try:
            from ..sources import intervals
            store = open_store(owner)
            last = float(store.get_meta("last_sync") or 0)
            # Backfill a year until a backfill has actually brought data in; an empty
            # pull (source not ready yet) must not mark history as done or wait an hour.
            backfilled = store.get_meta("backfilled") == "1"
            # Mornings: until last night's sleep/HRV has landed, check every 10 minutes.
            # Garmin only uploads it once the watch syncs to the phone.
            # Daytime (6 AM to 10 PM): every 15 minutes, so a workout shows up soon after
            # your watch syncs. Overnight: hourly. Mornings: every 10 until last night lands.
            from datetime import datetime as _dt
            from zoneinfo import ZoneInfo as _Z
            from ..config import settings as _settings
            hour = _dt.now(_Z(_settings().tz)).hour
            every = (MORNING_EVERY_S if not _have_last_night(store)
                     else 15 * 60 if 6 <= hour < 22 else REFRESH_EVERY_S)
            if backfilled and not force and time.time() - last < every:
                return
            days = 3 if backfilled else 365
            store.set_meta("sync_state", f"running since {int(time.time())}")
            client = intervals.IntervalsClient(key, athlete)
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
            bust(store)
            _warm(store, if_changed=True)
        except Exception as e:  # never take the site down over a sync
            print(f"sync failed: {e}")
            if store:
                try:
                    store.set_meta("sync_state", f"failed: {type(e).__name__}: {str(e)[:160]}")
                except Exception:
                    pass
        finally:
            lock.release()

    threading.Thread(target=run, daemon=True).start()


_warmed: dict = {}


def _warm(store=None, if_changed: bool = False) -> None:
    """Build today's page ahead of the first visitor. After a sync (if_changed), only when the
    sync actually brought something new or the day has turned: most 15-minute pulls bring
    nothing, and on a 0.1-CPU host a needless rebuild slows every page while it runs."""
    try:
        store = store or open_store()
        from ..service import data_version
        mark = (local_today().isoformat(), data_version(store))
        if if_changed and _warmed.get(store.owner) == mark:
            return
        p, rx = today_rx(store)
        cached(store, "dash", rx.date, lambda: dashboard.build(store, p, rx))
        cached(store, "story", rx.date, lambda: storymod.build(store, p))
        cached(store, "planx", rx.date, lambda: plan_with_changes(store))
        from .. import workouts as wk, motivation as mv, history as histmod, journal as jr
        cached(store, "workouts", rx.date, lambda: wk.build(store))
        cached(store, "motivation", rx.date, lambda: mv.build(p, rx, store))
        cached(store, "calls", rx.date, lambda: mv.calls(p, store, rx.date))
        cached(store, "patterns", rx.date, lambda: jr.patterns(store, p.wellness))
        cached(store, "hrhist", rx.date, lambda: dist.hr_histogram(store, p))
        for _k, _l, _d, _c in ROOM_WINDOWS:
            cached(store, f"hrwin:{_k}", rx.date, lambda d=_d: dist.hr_window(store, p, rx.date, d))
        _rooms_cached(store, p, rx, _after(store, p, rx, jr.load(store, rx.date)))
        ym = rx.date[:7]
        cached(store, f"month:{ym}", rx.date, lambda: histmod.month(p, ym, rx.date, _live_rx(store, ym), store))
        _warmed[store.owner] = mark
    except Exception as e:
        print(f"warm failed: {e}")


@app.on_event("startup")
def _startup() -> None:
    def all_():
        for o in _all_owners():
            try:
                st = open_store(o)
                if st.counts()["activities"]:
                    _warm(st)
            except Exception as e:
                print(f"warm {o or 'primary'} failed: {e}")
    threading.Thread(target=all_, daemon=True).start()


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
    # Render's own health check also lands here every few seconds. On a 0.1-CPU instance that
    # traffic must cost next to nothing: check whether a sync is due at most once a minute, and
    # answer from a copy at most 15 seconds old.
    now = time.time()
    if now - _health["checked"] >= 60:
        _health["checked"] = now
        _refresh_all()
    if _health["text"] and now - _health["at"] < 15:
        return _health["text"]
    _health.update(text=_healthz(), at=now)
    return _health["text"]


_health: dict = {"checked": 0.0, "text": "", "at": 0.0}


def _healthz() -> str:
    try:
        s = open_store("")
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


def _energy(store, p, rx):
    from ..analysis import energy as en
    from zoneinfo import ZoneInfo
    try:
        wk = float(store.get_meta("wake_hour") or 7.0)
    except ValueError:
        wk = 7.0
    try:
        from ..config import settings as _st
        return en.build(p, rx.date, datetime.now(ZoneInfo(_st().tz)).replace(tzinfo=None), wk)
    except Exception as e:
        print(f"energy failed: {e}")
        return None


_VWORD = {"rest": "Day off", "light": "Walk day", "easy": "Easy day", "steady": "Steady day", "hard": "Hard day"}


def _kind(text: str) -> str:
    t = text.lower()
    for k, w in (("hard", "hard"), ("steady", "steady"), ("easy", "easy"), ("light", "walk"), ("rest", "day off")):
        if w in t:
            return k
    return "easy"


def _sentences(text: str) -> list[str]:
    import re as _re
    return [x.strip().rstrip(".") for x in _re.split(r"(?<=[.!?])\s+", text or "") if x.strip()]


def _today_cards(p, rx, after) -> dict:
    try:
        return _today_cards_(p, rx, after)
    except Exception as e:
        import traceback
        print(f"today cards failed: {e!r}\n{traceback.format_exc()}")
        return {"vword": _VWORD.get(rx.verdict, "Today") + ".", "intro": rx.diagnosis or "", "target": "", "caption": "",
                "ways": [], "ways_big": "", "ways_sub": "", "tmw": [], "tonight": "", "trace": "", "under": None,
                "post_tmw": {"k": "easy", "word": "Tomorrow.", "detail": (after or {}).get("tomorrow", "") or ""}}


def _today_cards_(p, rx, after) -> dict:
    """Today's three cards, in words the template only has to place: the day, the ways to do it,
    and tomorrow; after a session, the score, what more you could do, and tomorrow re-decided."""
    import re as _re
    from . import charts
    c = rx.hr_ceiling
    a = charts.aim(rx)
    mn = charts.minimum_minutes(rx)
    mx_abs = charts.maximum_minutes(rx)
    cap = []
    if mn:
        cap.append(f"At least {mn} minutes holds your fitness" + (f"; {rx.max_minutes} builds it." if rx.max_minutes else "."))
    if mx_abs and rx.maximum:
        tail = " ".join(_sentences(rx.maximum)[1:])
        if tail:
            cap.append(tail.replace("Past that", f"Past {mx_abs}", 1) + ".")
    ways = []
    if rx.what:
        name, _, rest = rx.what.partition(",")
        name = _re.sub(r"^(A|An)\s+(easy|steady|hard|short|long)?\s*", "", name).strip()
        mins = _re.search(r"(\d+) minutes", rest or rx.what)
        ways.append({"name": name[:1].upper() + name[1:], "dur": (f"Up to {mins.group(1)} min" if mins else ""),
                     "why": (_sentences(rx.activity_why)[:1] or [""])[0], "pick": True, "no": False})
    for title, note, tone, short, k in rx.options or []:
        name, _, rest = title.partition(",")
        mins = _re.search(r"(\d+) minutes", rest)
        ways.append({"name": name.strip(), "dur": f"{mins.group(1)} min" if mins else "", "why": note,
                     "pick": False, "no": tone == "bad"})
    n_ok = len(ways)
    tmw = []
    for label, tone, text in rx.tomorrow or []:
        ss = _sentences(text)
        i = next((j for j, x in enumerate(ss) if "omorrow" in x), 0)
        k = _kind(ss[i] if ss else text)
        other = (ss[:i] or ss[i + 1:i + 2])
        tmw.append({"label": label, "k": k, "word": _VWORD[k].replace(" day", "").replace("Day off", "Day off"),
                    "detail": (other[0] + ".") if other else ""})
    out = {"vword": _VWORD.get(rx.verdict, "Today") + ".", "intro": f"{rx.why_need} {rx.why_story}".strip(),
           "target": charts.target_svg(rx, p), "caption": " ".join(cap), "ways": ways,
           "ways_big": {1: "One way.", 2: "Two ways.", 3: "Three ways.", 4: "Four ways."}.get(n_ok, f"{n_ok} ways."),
           "ways_sub": (f"All at {a}." if all(not w["no"] for w in ways) else f"All at {a}; one makes it harder.") if a and n_ok > 1 else "", "tmw": tmw,
           "tonight": (f"In bed by {rx.sleep.lights_out} for {rx.sleep_if[0][0][9:]}." if rx.sleep and getattr(rx.sleep, "lights_out", None) and rx.sleep_if else "")}
    M = (after or {}).get("main") if after and after.get("done") else None
    if M:
        tr = M.get("trace") or []
        out["trace"] = charts.trace_svg(tr, p, c)
        out["under"] = round(100 * sum(1 for _, v in tr if v <= c) / len(tr)) if tr and c else None
    if after and after.get("done"):
        t = after.get("tomorrow") or ""
        head, _, rest = t.partition(":") if ":" in t else t.partition(".")
        k = _kind(head)
        out["post_tmw"] = {"k": k, "word": _VWORD[k] + ".", "detail": (rest.strip()[:1].upper() + rest.strip()[1:] + ".") if rest.strip() else ""}
    return out


def _lift_today(store, p, rx, day: str | None = None, length: str | None = None, key: str | None = None):
    """Today's lift, adjusted for recovery and tomorrow: (Session | None, log, settings, next lift)."""
    from .. import strength as sg, journal as jr
    from ..prescribe import engine as eng
    st = sg.settings(store)
    d = date.fromisoformat(day or rx.date)
    lifts = getattr(p, "lifts", None) or []
    no = []
    for x in lifts:
        no += list(getattr(x.goal, "no_days", []) or [])
    key = key or sg.scheduled(store, lifts, d, no)
    nxt = None
    for i in range(1, 8):
        k2 = sg.scheduled(store, lifts, d + timedelta(days=i), no)
        if k2:
            nxt = (gl_day_name(d + timedelta(days=i), d), sg.SESSIONS[k2]["name"])
            break
    if not key:
        return None, None, st, nxt
    try:
        tomorrow = eng._next_verdict(p, rx, eng._rx_load(p, rx)) if st["train"] != "strength" else ""
    except Exception:
        tomorrow = ""
    js = getattr(p, "journal", None) or {}
    low = bool(rx.recovery and rx.recovery.reds >= 1) or jr.hrv_low(p.wellness, d.isoformat(), morning=True)
    sess = sg.session(store, key, d.isoformat(), rx.verdict, tomorrow, recovery_low=low,
                      sick=bool(js.get("sick")) or rx.verdict == "rest", length=length)
    return sess, sg.log_for(store, d.isoformat()), st, nxt


def gl_day_name(d: date, today: date) -> str:
    return "tomorrow" if (d - today).days == 1 else d.strftime("%A")


@app.get("/lift", response_class=HTMLResponse)
def lift_page(request: Request, v: str | None = None, s: str | None = None):
    from .. import strength as sg
    store = open_store()
    p, rx = today_rx(store)
    sess, lg, st, nxt = _lift_today(store, p, rx, length=v if v in ("full", "short") else None,
                                    key=s if s in sg.SESSIONS and s != "mobility" else None)
    has_plan = bool(getattr(p, "lifts", None))
    return _page(request, "lift.html", sess=sess, log=lg or {}, st=st, nxt=nxt, has_plan=has_plan, day=rx.date,
                 day_label=date.fromisoformat(rx.date).strftime("%A, %B %-d"), sessions=sg.SESSIONS, equip=sg.EQUIP)


@app.post("/lift")
async def lift_save(request: Request):
    """Autosave from the lift page: every set as you tick it, and 'done' when you finish."""
    import json as _json
    from fastapi.responses import JSONResponse
    from .. import strength as sg
    try:
        body = _json.loads((await request.body()).decode() or "{}")
    except ValueError:
        return JSONResponse({"ok": False}, status_code=400)
    store = open_store()
    day = local_today().isoformat()
    sets = {}
    for ex, rows in (body.get("sets") or {}).items():
        if ex in sg.LIB and isinstance(rows, list):
            clean_ = []
            for r in rows[:6]:
                try:
                    w = float(r[0]) if r[0] not in (None, "") else None
                    n = int(float(r[1])) if r[1] not in (None, "") else None
                except (TypeError, ValueError, IndexError):
                    w, n = None, None
                clean_.append([w, n, bool(r[2]) if len(r) > 2 else False])
            sets[ex] = clean_
    lg = {"session": str(body.get("session", ""))[:20], "mode": str(body.get("mode", ""))[:10], "sets": sets,
          "done": bool(body.get("done")), "mobility": [bool(x) for x in (body.get("mobility") or [])][:12]}
    sg.save_log(store, day, lg)
    if lg["done"]:
        bust(store)
    d, n = sg.done_sets(lg)
    return JSONResponse({"ok": True, "done": d, "of": n})


@app.post("/settings/training")
async def training_settings(request: Request):
    from .. import strength as sg
    form = parse_qs((await request.body()).decode())
    f = {k: (v[0] if v else "") for k, v in form.items()}
    store = open_store()
    sg.save_settings(store, train=f.get("train") if f.get("train") in sg.TRAIN else None,
                     equip=f.get("equip") if f.get("equip") in sg.EQUIP else None,
                     units=f.get("units") if f.get("units") in ("lb", "kg") else None,
                     length=f.get("length") if f.get("length") in ("full", "short") else None)
    if f.get("wake"):
        try:
            hh, mm = f["wake"].split(":")
            store.set_meta("wake_hour", str(int(hh) + int(mm) / 60))
        except ValueError:
            pass
    bust(store)
    return RedirectResponse("/me?saved=1", status_code=303)


@app.get("/", response_class=HTMLResponse)
def today(request: Request, d: str | None = None):
    """Today is the prescription (or, after a session, the review) and the check-in. The numbers live
    in the Dashboard; one quiet line links there."""
    if not _signed_in(request):
        return _site(request, "site_home.html")
    _refresh_in_background()
    store = open_store()
    p, rx = today_rx(store, d)
    dash = cached(store, "dash", rx.date, lambda: dashboard.build(store, p, rx))
    when = date.fromisoformat(rx.date)
    last = store.get_meta("last_sync")
    mins_ago = int((time.time() - float(last)) / 60) if last and float(last) > 0 else None
    lk = _sync_locks.get(current_owner.get())
    syncing = bool(lk and lk.locked())   # only a sync actually in progress; never a stale flag
    from .. import journal as jr
    entry = jr.load(store, rx.date)
    after = _after(store, p, rx, entry)
    energy = _energy(store, p, rx)
    body = []
    if dash.has_data:
        if dash.sleep.value not in ("", "—"):
            body.append(f"slept {dash.sleep.value}")
        if energy:
            body.append(f"energy {energy.now}")
        if dash.hrv.value not in ("", "—"):
            body.append(f"HRV {dash.hrv.value.replace(' ms', '')}")
    pats = cached(store, "patterns", rx.date, lambda: jr.patterns(store, p.wellness))
    return _page(request, "today.html", rx=rx, waiting=not _have_last_night(store), updated=mins_ago,
                 after=after, entry=entry, timeline=jr.timeline(entry), jnote=jr.effect(entry["tags"], jr.hrv_low(p.wellness, rx.date))[1] if entry else "", patterns=pats,
                 syncing=syncing, p=p, body=", ".join(body), cards=_today_cards(p, rx, after),
                 day_label=when.strftime("%A, %B %-d"))


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(request: Request):
    """Where you dig in: status tiles, energy, your heart over any window, last night read, sleep bank,
    your cycle, what you're good at, activity and recovery, and what matters this week."""
    store = open_store()
    p, rx = today_rx(store)
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
    from .. import journal as jr
    after = _after(store, p, rx, jr.load(store, rx.date))
    return _page(request, "dashboard.html", rx=rx, d=dash, p=p, recent=recent, energy=_energy(store, p, rx),
                 spark=dashboard.spark, rooms=_rooms_cached(store, p, rx, after),
                 st=st, hist=storymod.history_chart(st),
                 sleep_chart=sleepmod.chart(rx.sleep) if rx.sleep else "",
                 base_need=sleepmod._hm(rx.sleep.base) if rx.sleep else "",
                 day_label=when.strftime("%A, %B %-d"))


@app.get("/me", response_class=HTMLResponse)
def me_page(request: Request):
    return _page(request, "me.html")


def _plan_num(x):
    try:
        v = float(x)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def _plan_form(form: dict) -> dict:
    return {k: (v[0] if v else "").strip() for k, v in form.items()}


def _plan_apply(g, form: dict, f: dict) -> None:
    """The adjustable fields: date, target, weekly hours cap, sport, days you can't train, weights."""
    if "date" in f:
        g.target_date = f["date"] or (g.target_date if g.kind == "event" else None)
    if "target" in f:
        g.target = f["target"][:120]
    if "max_hours" in f:
        g.max_hours = _plan_num(f["max_hours"])
    if "sport" in f and f["sport"] in ("", "run", "bike", "ski"):
        from .. import goals as gl
        g.sport = f["sport"] or (gl.EVENT_SPORT.get((g.event or "").lower(), "") if g.kind == "event" else "")
    if f.get("edit"):
        g.no_days = sorted({int(x) for x in form.get("no", []) if x.isdigit() and 0 <= int(x) <= 6})
    if "long_day" in f:
        g.long_day = int(f["long_day"]) if f["long_day"].isdigit() and 0 <= int(f["long_day"]) <= 6 else None
    if g.kind == "weight":
        g.start_weight = _plan_num(f.get("w_now")) or g.start_weight
        g.target_weight = _plan_num(f.get("w_target")) or g.target_weight
    if g.kind == "strength" and f.get("lift_days", "").isdigit():
        g.lift_days = max(1, min(5, int(f["lift_days"])))


def _lift_week(store, g) -> list[tuple[str, str]]:
    """A strength plan's week as the plan page shows it: (weekday, session)."""
    from .. import strength as sg, goals as gl
    from types import SimpleNamespace
    mon = local_today() - timedelta(days=local_today().weekday())
    out = []
    for i in range(7):
        d = mon + timedelta(days=i)
        k = sg.scheduled(store, [SimpleNamespace(goal=g)], d, g.no_days)
        if k:
            out.append((gl.DAY_NAMES[i], sg.SESSIONS[k]["name"]))
    return out


def _plan_row(gl, p, g, today):
    prog = gl.build(p, g, today)
    x, y = gl.progress(g, today)
    return {"g": g, "prog": prog, "week": x, "of": y}


@app.get("/plan", response_class=HTMLResponse)
def plan_list(request: Request):
    from .. import goals as gl
    store = open_store()
    plans = gl.all_plans(store)
    if not plans:
        return _page(request, "plan.html", mode="new", canned=gl.CANNED, has_plans=False)
    p, rx = today_rx(store)
    today = local_today()
    rows = [_plan_row(gl, p, g, today) for g in plans]
    groups = [("Active", [r for r in rows if gl.is_live(r["g"], today)]),
              ("Drafts", [r for r in rows if r["g"].status == "draft"]),
              ("Stopped", [r for r in rows if r["g"].status == "stopped" and not gl.is_live(r["g"], today)])]
    lead = getattr(p, "program", None)
    return _page(request, "plan.html", mode="list", groups=groups, date=date, today=today,
                 lead=lead.goal.id if lead else None)


@app.get("/plan/new", response_class=HTMLResponse)
def plan_new(request: Request):
    from .. import goals as gl
    return _page(request, "plan.html", mode="new", canned=gl.CANNED, has_plans=bool(gl.all_plans(open_store())))


@app.post("/plan")
async def plan_create(request: Request):
    """A goal from the picker becomes a draft plan to adjust and commit."""
    from .. import goals as gl
    form = parse_qs((await request.body()).decode())
    f = _plan_form(form)
    today = local_today()
    if f.get("text"):                                # the same path as changing a plan in words
        g, _ = gl.from_text(f["text"], today, _plan_num(f.get("w_now")))
    else:
        c = next((c for c in gl.CANNED if c["id"] == f.get("canned")), gl.CANNED[0])
        g = gl.Goal(c["kind"], event=c.get("event", ""), sport=gl.EVENT_SPORT.get(c.get("event", ""), ""),
                    created=today.isoformat(), lift_days=3 if c["kind"] == "strength" else None,
                    target_date=(today + timedelta(weeks=c["weeks"])).isoformat() if c.get("weeks") else None)
        if g.kind == "event" and g.target_date:              # events are on weekends: the Sunday of that week
            td = date.fromisoformat(g.target_date)
            g.target_date = (td + timedelta(days=6 - td.weekday())).isoformat()
    if f.get("date"):
        g.target_date = f["date"]
    if f.get("target"):
        g.target = f["target"][:120]
    if g.kind == "weight":
        g.start_weight = _plan_num(f.get("w_now")) or g.start_weight
        g.target_weight = _plan_num(f.get("w_target")) or g.target_weight
    g.status = "draft"
    store = open_store()
    g = gl.save_plan(store, g)
    bust(store)
    return RedirectResponse(f"/plan/{g.id}", status_code=303)


@app.get("/plan/{pid}", response_class=HTMLResponse)
def plan_detail(request: Request, pid: str):
    from .. import goals as gl
    store = open_store()
    g = gl.get(store, pid)
    if not g:
        return RedirectResponse("/plan", status_code=303)
    p, rx = today_rx(store)
    today = local_today()
    row = _plan_row(gl, p, g, today)
    mon = (today - timedelta(days=today.weekday())).isoformat()
    done = sum((a.moving_s or 0) for a in p.activities if a.date >= mon and a.sport != "walk") / 3600
    others = [x for x in gl.active(store, today) if x.id != g.id]
    q = request.query_params
    lift_week = _lift_week(store, g) if g.kind == "strength" else []
    return _page(request, "plan.html", mode="detail", r=row, prog=row["prog"], g=g, date=date, today=today, lift_week=lift_week,
                 done_h=round(done, 1), live=gl.is_live(g, today), others=others, days=gl.DAY_NAMES,
                 just=q.get("ok", ""), changed=q.getlist("c")[:12], notes=q.getlist("n")[:6],
                 said=q.get("said", "")[:400], ask=q.get("ask", ""), example=gl.EXAMPLE)


@app.post("/plan/{pid}/say")
async def plan_say(request: Request, pid: str):
    """Change a plan in your own words: the same parser that builds one from words."""
    from urllib.parse import urlencode
    from .. import goals as gl
    store = open_store()
    g = gl.get(store, pid)
    if not g:
        return RedirectResponse("/plan", status_code=303)
    text = _plan_form(parse_qs((await request.body()).decode())).get("say", "")[:400]
    if not text:
        return RedirectResponse(f"/plan/{pid}", status_code=303)
    new, ch = gl.apply_text(g, text, local_today())
    if ch.stop:                                      # never stop silently: the page asks first
        return RedirectResponse(f"/plan/{pid}?" + urlencode({"ask": "stop", "said": text}), status_code=303)
    q = [("n", n) for n in ch.notes]
    if ch:
        gl.save_plan(store, new)                     # same id, commit date and status: history stays
        bust(store)
        q = [("ok", "said")] + [("c", c) for c in ch] + q
        return RedirectResponse(f"/plan/{pid}?" + urlencode(q), status_code=303)
    q.append(("said", text))                         # nothing changed: keep the words, say why
    return RedirectResponse(f"/plan/{pid}?" + urlencode(q) + "#adjust", status_code=303)


async def _plan_edit(request: Request, pid: str):
    from .. import goals as gl
    store = open_store()
    g = gl.get(store, pid)
    if not g:
        return None, store
    form = parse_qs((await request.body()).decode(), keep_blank_values=True)
    _plan_apply(g, form, _plan_form(form))
    gl.save_plan(store, g)
    return g, store


@app.post("/plan/{pid}/edit")
async def plan_edit(request: Request, pid: str):
    g, store = await _plan_edit(request, pid)
    bust(store)
    return RedirectResponse(f"/plan/{pid}?ok=saved" if g else "/plan", status_code=303)


@app.post("/plan/{pid}/commit")
async def plan_commit(request: Request, pid: str):
    from .. import goals as gl
    g, store = await _plan_edit(request, pid)
    if g:
        gl.commit(store, pid, local_today())
    bust(store)
    return RedirectResponse(f"/plan/{pid}?ok=committed" if g else "/plan", status_code=303)


@app.post("/plan/{pid}/stop")
def plan_stop(pid: str):
    from .. import goals as gl
    store = open_store()
    gl.stop(store, pid, local_today())
    bust(store)
    return RedirectResponse("/plan", status_code=303)


@app.post("/plan/{pid}/delete")
def plan_delete(pid: str):
    from .. import goals as gl
    store = open_store()
    gl.delete(store, pid)
    bust(store)
    return RedirectResponse("/plan", status_code=303)


ROOM_WINDOWS = [("today", "Today", 1, "where your heart was today"),
                ("3d", "3 days", 3, "where you trained, last 3 days"),
                ("7d", "7 days", 7, "where you trained, last 7 days"),
                ("30d", "30 days", 30, "where you trained, last 30 days"),
                ("all", "All time", None, "where you've trained, all time")]


def _after(store, p, rx, entry):
    """today_after, built once per data version and journal state (it reviews today's streams).
    Today's energy line stays live: it moves with the clock."""
    import json as _json
    jkey = _json.dumps(entry, sort_keys=True, default=str) if entry else ""
    return cached(store, f"after:{hash(jkey)}", rx.date, lambda: today_after(store, p, rx))


def _rooms_cached(store, p, rx, after) -> list[dict]:
    done = bool(after and after.get("done"))
    return cached(store, f"rooms:{done}", rx.date, lambda: _rooms(store, p, rx, after))


def _rooms(store, p, rx, after) -> list[dict]:
    """The heart chart for each window (today, 3, 7, 30 days, all time), so the toggle
    switches without a reload. The default: today once you've trained, otherwise 30 days."""
    out = []
    for key, label, days, cap in ROOM_WINDOWS:
        hist = cached(store, f"hrwin:{key}", rx.date, lambda d=days: dist.hr_window(store, p, rx.date, d))
        if hist.sum() < 300:
            out.append({"key": key, "label": label, "svg": "", "empty": True})
            continue
        out.append({"key": key, "label": label, "empty": False,
                    "svg": dashboard.room(rx, p, hist, None, hist_label=cap)})
    trained = after and after.get("done") and not out[0]["empty"]
    default = "today" if trained else "30d"
    if next(r for r in out if r["key"] == default)["empty"]:
        default = next((r["key"] for r in out if not r["empty"]), default)
    for r in out:
        r["on"] = r["key"] == default
    return out


def _live_rx(store, ym: str) -> dict:
    """The prescriptions Ceiling actually gave each morning of a month (saved from today on)."""
    import json as _json
    out = {}
    for r in store.db.execute("SELECT key, value FROM meta WHERE key LIKE ?", (f"rx:{ym}-%",)):
        try:
            out[r["key"][3:]] = tuple(_json.loads(r["value"]))
        except Exception:
            pass
    return out


@app.get("/calendar/history", response_class=HTMLResponse)
def history_page(request: Request, m: str | None = None):
    from .. import history as histmod
    store = open_store()
    p, rx = today_rx(store)
    ym = m if (m and len(m) == 7) else rx.date[:7]
    mon = cached(store, f"month:{ym}", rx.date, lambda: histmod.month(p, ym, rx.date, _live_rx(store, ym), store))
    return _page(request, "history.html", mon=mon, active="calendar")


@app.get("/calendar", response_class=HTMLResponse)
def plan_page(request: Request, m: str | None = None):
    from ..prescribe import plan as planmod
    store = open_store()
    p, rx = today_rx(store)
    pl, changed = cached(store, "planx", rx.date, lambda: plan_with_changes(store))
    try:                                              # the whole plan: firm days, then pencilled in
        cal = cached(store, "outlook2", rx.date, lambda: planmod.months(planmod.outlook(p, rx, plan=pl)))
    except Exception as e:
        print(f"outlook failed: {e}")
        cal = []
    yms = [x["ym"] for x in cal]
    mi = yms.index(m) if m in yms else 0
    hist = None
    try:                                              # what today's synced workouts actually were
        after = cached(store, "cal_after", rx.date, lambda: today_after(store, p, rx))
    except Exception:
        after = None
    from ..history import SHORT
    from ..prescribe.engine import SUBTYPE
    acts = sorted((a for a in p.activities if a.date == rx.date and (a.moving_s or 0) >= 10 * 60),
                  key=lambda a: a.start_local or "")       # the same order today_after lists them in
    done = []
    for a, s in zip(acts, (after or {}).get("sessions", [])):
        if not s.get("walk"):
            k = SUBTYPE.get(a.raw_type or "", a.sport)
            done.append(dict(s, sport=SHORT.get(k, k.capitalize())))
    past = {}                                         # this week's days already behind you, scored like History
    if cal and cal[mi]["ym"] == rx.date[:7]:
        from .. import history as histmod
        gone = [c["date"] for w in cal[mi]["weeks"] if w["shown"] for c in w["cells"] if c and c.get("past") and c.get("date")]
        try:
            past = cached(store, "cal_past", rx.date, lambda: {
                d: histmod.day_cell(p, d, rx.date, _live_rx(store, rx.date[:7]), store) for d in gone})
        except Exception as e:
            print(f"cal past failed: {e}")
    email = auth.verify("session", request.cookies.get(auth.COOKIE)) if auth.enabled() else None
    feed = None
    if email:
        tok = auth.sign("cal", email, 10 * 365 * 24 * 3600)
        feed = f"{request.url.netloc}/plan.ics?k={tok}"
    return _page(request, "calendar.html", plan=pl, changed=changed, hist=hist, feed=feed, date=date, done=done, past=past,
                 cal=cal[mi] if cal else None, cal_prev=yms[mi - 1] if mi > 0 else None,
                 cal_next=yms[mi + 1] if mi + 1 < len(yms) else None, cal_end=date.fromisoformat(cal[-1]["end"]).strftime("%B %-d, %Y") if cal else "",
                 has_plan=bool(getattr(p, "programs", None) or getattr(p, "program", None)))


@app.get("/plan.ics")
def plan_ics(k: str = ""):
    """Calendar subscription. Calendar apps can't sign in, so the link carries a long-lived signed key."""
    from ..prescribe import plan as planmod
    email = auth.verify("cal", k) if auth.enabled() else None
    if not (os.getenv("RX_OPEN") == "1" or email):
        return Response("Not found.", status_code=404)
    store = open_store(owner_of(email))
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
def data_page(request: Request, msg: str | None = None):
    """What Ceiling actually received, day by day. For checking a missing night.
    Also where a new athlete connects their own data."""
    import json as _json
    store = open_store()
    owner = current_owner.get()
    connected = bool(not owner or store.get_meta("intervals_key"))
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
    try:
        err = _json.loads(store.get_meta("last_error") or "null")
    except ValueError:
        err = None
    return _page(request, "data.html", err=err, rows=rows, missing=[d for d in days if d not in have],
                 today=local_today().isoformat(), state=store.get_meta("sync_state") or "",
                 progress=store.get_meta("sync_progress") or "",
                 last=int((time.time() - float(last)) / 60) if last and float(last) > 0 else None,
                 owner=owner, connected=connected, msg=msg,
                 athlete=store.get_meta("intervals_athlete") or "",
                 empty=not (store.counts()["activities"] or store.counts()["wellness"]))


@app.post("/journal")
async def journal_save(request: Request):
    """A check-in. Called by the page in the background (no reload); returns what was understood."""
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo as _Z
    from ..config import settings as _settings
    from fastapi.responses import JSONResponse
    from .. import journal as jr
    form = parse_qs((await request.body()).decode())
    text = (form.get("text", [""])[0] or "").strip()
    taps = {k: (form.get(k, [""])[0] or "") for k in ("feel", "drinks", "food")}
    now = _dt.now(_Z(_settings().tz))
    store = open_store()
    day = now.date().isoformat()
    at = now.strftime("%-I:%M %p")                      # 12-hour, athlete's local time
    saved = bool(text or any(taps.values()))
    if saved:
        jr.save(store, day, text, taps, at)
        bust(store)                                     # tomorrow (and this morning, if early) re-reads it
    if "application/json" in request.headers.get("accept", ""):
        entry = jr.load(store, day) or {}
        try:
            p_, _ = today_rx(store)
            low = jr.hrv_low(p_.wellness, day)
        except Exception:
            low = False
        cap, note = jr.effect(entry.get("tags", {}), low)
        log = entry.get("log") or []
        return JSONResponse({"ok": saved, "at": at, "note": note,
                             "entry": jr.view(log[-1]) if saved and log else None})
    return RedirectResponse("/#journal", status_code=303)


@app.post("/connect")
async def connect(request: Request):
    """Connect (or change) your intervals.icu account. The key is encrypted before it's stored,
    in your own tables, and only ever sent back to intervals.icu."""
    form = parse_qs((await request.body()).decode())
    athlete = (form.get("athlete", [""])[0] or "").strip()
    key = (form.get("key", [""])[0] or "").strip()
    owner = current_owner.get()
    if not owner:
        return RedirectResponse("/data?msg=primary", status_code=303)
    if not athlete or not key:
        return RedirectResponse("/data?msg=missing", status_code=303)
    from ..sources import intervals
    try:
        intervals.IntervalsClient(key, athlete).wellness(local_today().isoformat(), local_today().isoformat())
    except Exception:
        return RedirectResponse("/data?msg=bad", status_code=303)
    st = open_store(owner)
    st.set_meta("intervals_athlete", athlete)
    st.set_meta("intervals_key", auth.seal(key))
    st.set_meta("backfilled", "0")
    _refresh_in_background(force=True, owner=owner)
    return RedirectResponse("/data?msg=ok", status_code=303)


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
    return _page(request, "workouts.html", rows=rows, agg=agg, aggnote=note, scale=scale, date=date, today_iso=rx.date)


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
    from .. import motivation as mv
    store = open_store()
    p, rx = today_rx(store)
    m = cached(store, "motivation", rx.date, lambda: mv.build(p, rx, store))
    c = cached(store, "calls", rx.date, lambda: mv.calls(p, store, rx.date))
    return _page(request, "progress.html", p=p, m=m, calls=c)


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
