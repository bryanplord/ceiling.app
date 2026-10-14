"""Shared glue used by both the CLI and the web app."""
from __future__ import annotations

import threading
from datetime import date, timedelta

from . import profile as prof
from .analysis.recovery import assess
from .config import settings
from .prescribe.engine import build_rx
from .prescribe.score import score
from .store import Store
from .clock import local_today


_shared: Store | None = None


import contextvars

current_owner: contextvars.ContextVar[str] = contextvars.ContextVar("current_owner", default="")
_views: dict = {}


def owner_of(email: str | None) -> str:
    """Which athlete an account is. The primary athlete (RX_PRIMARY_EMAIL, Bryan by default)
    keeps the original data; every other account gets its own, empty until they connect."""
    import os
    import re
    if not email:
        return ""
    primary = {e.strip().lower() for e in os.getenv("RX_PRIMARY_EMAIL", "bryanplord@gmail.com").split(",")}
    e = email.strip().lower()
    return "" if e in primary else re.sub(r"[^a-z0-9]", "", e.split("@")[0])[:24] or "user"


def open_store(owner: str | None = None) -> Store:
    """Plain SQLite: a fresh connection each time. Turso: one shared replica per
    process (opening one means pulling from Turso, too slow to do per request).
    Either way you get the signed-in athlete's view and nobody else's."""
    global _shared
    owner = current_owner.get() if owner is None else owner
    s = settings()
    if not s.turso_url:
        base = Store(s.db_path)
        return base.view(owner)
    if _shared is None:
        _shared = Store(s.data_dir / "replica.db", s.turso_url, s.turso_token)
    if owner not in _views:
        _views[owner] = _shared.view(owner)
    return _views[owner]


_built: dict = {}
_build_lock = threading.RLock()


_ver_cache: dict = {}


def bust(store: Store | None = None) -> None:
    """Forget the remembered data version (after a sync, a check-in or a goal change)."""
    if store is None:
        _ver_cache.clear()
    else:
        _ver_cache.pop(store.owner, None)


def data_version(store: Store) -> tuple:
    """Changes whenever a sync brings anything in. Every cached page is keyed on it, so it's
    read many times per request: remembered for 20 seconds rather than queried each time."""
    import time as _t
    hit = _ver_cache.get(store.owner)
    if hit and _t.time() - hit[0] < 20:
        return hit[1]
    v = _data_version(store)
    _ver_cache[store.owner] = (_t.time(), v)
    return v


def _data_version(store: Store) -> tuple:
    c = store.counts()
    # Fingerprint of what the pages are built from, NOT the time of the last sync: the morning
    # pull runs every 10 minutes and must not throw away every cached page when nothing changed.
    since = (local_today() - timedelta(days=3)).isoformat()
    recent = tuple((w.date, w.hrv, w.rhr, w.sleep_score, w.sleep_secs) for w in store.wellness(since))
    return (store.owner, c["activities"], c["streams"], c["wellness"], hash(recent), store.get_meta("goal") or "",
            store.get_meta("plans") or "")


def today_rx(store: Store, day: str | None = None):
    """Profile + prescription for a day, rebuilt only when the data changes.
    Building means reading every heart-rate stream, so it must not run per request."""
    day = day or local_today().isoformat()
    key = (day, data_version(store))
    with _build_lock:
        hit = _built.get(key)
        if hit is None:
            p = prof.build(store, day)
            p.programs = []
            try:
                from . import goals as _gl
                from datetime import date as _date
                d_ = _date.fromisoformat(day)
                # Every committed plan live on this day. p.program is the lead one, so older callers
                # keep working; anything that needs all of them uses goals.daily_all(p.programs, day).
                # One plan that fails to build must not take the others with it.
                for g in _gl.active(store, d_):
                    try:
                        p.programs.append(_gl.build(p, g, d_))
                    except Exception as e:
                        import traceback
                        print(f"goal failed: plan {g.id} ({g.kind} {g.event}): {e!r}\n{traceback.format_exc()}")
                p.program = _gl.primary(p.programs, d_)
            except Exception as e:
                import traceback
                print(f"goal failed: {e!r}\n{traceback.format_exc()}")
            hit = (p, build_rx(p, day))
            while len(_built) > 120:                 # drop the oldest, never everything at once
                _built.pop(next(iter(_built)))
            _built[key] = hit
            if day == local_today().isoformat():     # keep what today actually prescribed, for History
                try:
                    import json as _json
                    rx_ = hit[1]
                    store.set_meta(f"rx:{day}", _json.dumps([rx_.verdict, rx_.hr_low, rx_.hr_ceiling, rx_.max_minutes,
                                                          rx_.sport, [[o[3], o[4]] for o in (rx_.options or [])]]))
                except Exception:
                    pass
    return hit


def cached(store: Store, name: str, day: str, make):
    """Memoize anything derived from a day's profile under the same data version."""
    key = (name, day, data_version(store))
    with _build_lock:
        if key in _built:
            return _built[key]
    val = make()                 # built outside the lock so one slow build never blocks every page
    with _build_lock:
        return _built.setdefault(key, val)


def peek(store: Store, name: str, day: str):
    """A cached value if it's ready, else None (never builds)."""
    with _build_lock:
        return _built.get((name, day, data_version(store)))


def profile_for(store: Store, day: str | None = None):
    return today_rx(store, day)[0]


def scored_day(store: Store, day: str):
    """Recompute the prescription as it stood that morning, then score each session."""
    p, rx = today_rx(store, day)
    out = []
    for a in store.activities(since=day):
        if a.date != day:
            continue
        st = store.stream(a.id)
        out.append((a, score(rx, st[0], st[1], a.sport) if st else None))
    return rx, out


def recovery_history(store: Store, days: int = 30):
    ws = store.wellness()
    end = local_today()
    return [assess(ws, (end - timedelta(days=i)).isoformat()) for i in range(days - 1, -1, -1)]


def plan_with_changes(store: Store, day: str | None = None):
    """Today's plan, marked against yesterday's plan (rebuilt as it stood yesterday morning)."""
    from datetime import date as _d
    from .prescribe import plan as planmod
    p, rx = today_rx(store, day)
    pl = planmod.build(p, rx)
    y = (_d.fromisoformat(rx.date) - timedelta(days=1)).isoformat()
    try:
        py, rxy = today_rx(store, y)
        changed = planmod.adjust(p, rx, pl, planmod.build(py, rxy))
    except Exception:
        changed = []
    return pl, changed


def plan_history(store: Store, days: int = 7):
    """The last few days as a calendar: what the morning prescribed (and whether that changed from
    the day before's plan, and why), what you actually did, and whether you recovered from it."""
    from datetime import date as _d
    from .prescribe import plan as planmod
    from .prescribe.engine import ORDER, _rx_load, REST, LIGHT
    end = _d.fromisoformat(local_today().isoformat())
    p_now, _ = today_rx(store)
    w = sorted(p_now.wellness, key=lambda x: x.date)

    def usual(field, before):
        vals = [getattr(x, field) for x in w if x.date < before and getattr(x, field)][-60:]
        return sum(vals) / len(vals) if len(vals) >= 14 else None

    out = []
    for k in range(days, 0, -1):
        d = (end - timedelta(days=k)).isoformat()
        y = (end - timedelta(days=k + 1)).isoformat()
        nxt = (end - timedelta(days=k - 1)).isoformat()
        try:
            p, rx = today_rx(store, d)
        except Exception:
            continue
        row = {"date": d, "now": rx.verdict, "what": (rx.what or rx.headline).rstrip("."), "changed": False,
               "was": None, "was_title": "", "why": ""}
        try:
            py, rxy = today_rx(store, y)
            prev = planmod.build(py, rxy)
            planned = next((x for x in prev.days if x.date == d), None)
            if planned and not planned.reason.startswith("You almost never") and planned.verdict != rx.verdict:
                up = 1 if ORDER.index(rx.verdict) > ORDER.index(planned.verdict) else -1
                row.update(changed=True, was=planned.verdict, was_title=planned.title, why=planmod.explain(up, planmod.causes(p, rx, prev), rx))
        except Exception:
            pass
        # what you did
        acts = [a for a in p_now.activities if a.date == d]
        did = []
        for a in acts:
            h, m = divmod(round((a.moving_s or 0) / 60), 60)
            dur = f"{h}h {m:02d}m" if h else f"{m} min"
            did.append(f"{a.name or a.sport.capitalize()}, {dur}" + (f", avg {a.avg_hr:.0f}" if a.avg_hr else ""))
        load = p_now.daily_load.get(d, 0.0)
        plan_load = _rx_load(p, rx) if rx.verdict != REST else 0.0
        main = max((a for a in acts if a.sport != "walk"), key=lambda a: a.moving_s or 0, default=None)
        hot = bool(main and main.avg_hr and rx.hr_ceiling and main.avg_hr > rx.hr_ceiling + 3)
        if rx.verdict in (REST, LIGHT):
            ok = load <= max(30.0, plan_load * 1.5) and main is None
            work = ("Rested as planned", "good") if ok else ("Trained on a recovery day", "bad")
        elif hot:
            work = (f"Too hard: averaged {main.avg_hr:.0f}, ceiling {rx.hr_ceiling}", "bad")
        elif False:
            ok = load <= max(30.0, plan_load * 1.5)
            work = ("Rested as planned", "good") if ok else ("Trained on a recovery day", "bad")
        elif load < 5:
            work = ("Skipped", "muted")
        else:
            r = load / plan_load if plan_load else 1.0
            work = (("Too long", "bad") if r >= 1.3 else ("Too easy", "ok") if r <= 0.6 else ("Nailed it", "good"))
        # did you recover from it (the next morning against your normal)
        rec = None
        nw = next((x for x in w if x.date == nxt), None)
        if nxt <= end.isoformat() and nw and nw.hrv:
            u = usual("hrv", nxt)
            if u:
                rec = (f"Recovered: HRV {nw.hrv:.0f} next morning (usual {u:.0f})", "good") if nw.hrv >= u * 0.95 else \
                      (f"Didn't recover: HRV {nw.hrv:.0f} next morning (usual {u:.0f})", "bad") if nw.hrv < u * 0.88 else \
                      (f"Mostly recovered: HRV {nw.hrv:.0f} next morning (usual {u:.0f})", "ok")
        if work[1] == "good" and rec and rec[1] == "good":
            head = "You nailed the workout and the recovery."
        elif work[1] == "good" and rec and rec[1] == "bad":
            head = "Right workout; recovery lagged."
        elif work[1] == "bad" and rec and rec[1] == "bad":
            head = "Too much, and it showed the next morning."
        elif work[1] == "bad":
            head = "Went over, but you got away with it." if rec and rec[1] == "good" else "Went over the plan."
        else:
            head = ""
        row.update(did=did, work=work[0], work_tone=work[1], rec=rec[0] if rec else "", rec_tone=rec[1] if rec else "",
                   head=head)
        out.append(row)
    return out


def today_after(store: Store, p, rx) -> dict | None:
    """Once today's session has synced, Today stops being about what to do and becomes:
    how it went (a score out of 100, what went right, what to fix, the numbers worth knowing),
    what more you could do if you want it, what happens if you stop here, and tomorrow,
    re-decided from what you actually did (and tonight's journal)."""
    from .prescribe import engine as eng
    from .workouts import zone_minutes, bands
    from .review import review
    from . import journal as jr
    acts = [a for a in p.activities if a.date == rx.date and (a.moving_s or 0) >= 10 * 60]
    entry = jr.load(store, rx.date)
    cap, note = jr.effect(entry["tags"]) if entry else (None, "")
    if not acts and not cap:
        return None
    out = {"sessions": [], "journal_note": note}
    load = 0.0
    trained_min = 0
    for a in sorted(acts, key=lambda a: a.start_local or ""):
        load += p.session_load.get(a.id, 0.0) or eng._est_load(p, (a.moving_s or 0) / 60, a.avg_hr or 0)
        if a.sport != "walk":
            trained_min += round((a.moving_s or 0) / 60)
        h, m = divmod(round((a.moving_s or 0) / 60), 60)
        row = {"name": a.name or a.sport.capitalize(), "dur": f"{h}h {m:02d}m" if h else f"{m} min",
               "avg": round(a.avg_hr) if a.avg_hr else None, "score": None, "walk": a.sport == "walk",
               "time": (a.start_local or "")[11:16]}
        st = store.stream(a.id) if a.sport != "walk" else None
        if st:
            try:
                rv = review(p, rx, a, st, store.raw(a.id))
            except Exception as e:                      # a review must never take Today down
                print(f"review failed: {e}")
                rv = None
            if rv:
                mins = zone_minutes(p, st[0], st[1])
                tot = sum(mins) or 1
                row.update(target=rv.target, score=rv.score, word=rv.word, tone=rv.tone, head=rv.headline, right=rv.right,
                           fix=rv.fix, stats=rv.stats, parts=rv.parts,
                           zones=[(n, c, round(mm), mm / tot * 100) for (n, c, _, _), mm in zip(bands(p), mins)])
        out["sessions"].append(row)
    nxt = (date.fromisoformat(rx.date) + timedelta(days=1)).isoformat()
    planned = eng._next_verdict(p, rx, eng._rx_load(p, rx))
    now = eng._next_verdict(p, rx, load) if acts else planned
    why = ""
    if cap and eng.ORDER.index(now) > eng.ORDER.index(cap):
        now, why = cap, "because of what you logged tonight"
    elif acts and now != planned:
        why = "because of what you actually did today"
    trained = [s for s in out["sessions"] if not s["walk"]]
    scored = [s for s in trained if s.get("score") is not None]
    main = max(scored, key=lambda s: s["score"] if len(scored) == 1 else 0, default=None)
    out.update(tomorrow=eng._session_short(p, now, nxt), changed=now != planned, why=why,
               was=eng._session_short(p, planned, nxt) if now != planned else "", done=bool(trained),
               main=main, trained_min=trained_min)
    if trained:
        out.update(more=_more_today(p, rx, load, trained_min, now, nxt, main), stop=_stop_here(p, rx, now, nxt, main, trained_min))
    return out


def _aday(v: str) -> str:
    return {"rest": "a day off", "light": "a walk day", "easy": "an easy day", "steady": "a steady day",
            "hard": "a hard day"}.get(v, v)


def _more_today(p, rx, load: float, done_min: int, tomorrow_now: str, nxt: str, main: dict | None = None) -> dict:
    """If you want more today: a few ideas, and what each would do to tomorrow (run through the
    engine, not guessed). Never framed as a gap: today's session is judged on what you picked."""
    from .prescribe import engine as eng
    ceil = rx.hr_ceiling or 130
    easy_top = eng.easy_band(p.zones)[1]
    hot = main and main.get("score") is not None and main["parts"]["Ceiling"] < 30
    if rx.verdict in (eng.REST, eng.LIGHT) or hot:
        return {"ideas": [("A walk", f"As long as you like, under {min(ceil, easy_top)}. Free: no cost to tomorrow.", "good"),
                          ("15 minutes of mobility", "Hips, calves, upper back.", "good")],
                "line": "A walk or some mobility. Anything harder comes out of tomorrow."}
    if rx.verdict == eng.HARD:
        return {"ideas": [("A 20-minute cool-down spin or walk", f"Under {easy_top}. Flushes the legs, costs nothing.", "good"),
                          ("15 minutes of mobility", "Hips, calves, upper back. Helps the hard work land.", "good"),
                          ("Not: more hard work", "It costs tomorrow and buys nothing extra today.", "bad")],
                "line": "A cool-down or some mobility. The hard work is done."}
    room = max(20, (rx.max_minutes or 60) - done_min)
    extra = min(room, 45)
    v_more = eng._next_verdict(p, rx, load + eng._est_load(p, extra, ceil - 5))
    v_big = eng._next_verdict(p, rx, load + eng._est_load(p, extra + 45, ceil + 8))
    ideas = [(f"An easy {extra}-minute spin or hike, under {ceil}",
              "Tomorrow " + (f"stays {_aday(tomorrow_now)}." if v_more == tomorrow_now else f"becomes {_aday(v_more)}."), "good"),
             ("20–30 minutes of strength", "Squats, lunges, step-ups, core. Doesn't touch tomorrow, and it's what keeps "
              "legs and back durable for the long haul.", "good"),
             ("A walk or mobility", "Free: no cost to tomorrow.", "good")]
    if v_big != tomorrow_now:
        ideas.append((f"Not: another long or hard session", f"That turns tomorrow from {_aday(tomorrow_now)} into {_aday(v_big)}.", "bad"))
    return {"ideas": ideas, "line": "Today's session is done. A few ideas that won't cost tomorrow:"}


def _stop_here(p, rx, tomorrow_now: str, nxt: str, main: dict | None, done_min: int) -> str:
    """If you do nothing more today: what that means. Plainly, no guilt."""
    from .prescribe import engine as eng
    score = main["score"] if main and main.get("score") is not None else None
    hot = main and main.get("parts") and main["parts"]["Ceiling"] < 30
    if rx.verdict in (eng.REST, eng.LIGHT):
        return f"The right call: today was for recovering. Tomorrow is {_aday(tomorrow_now)} as it stands."
    if hot:
        return (f"The right call. You went harder than {_aday(rx.verdict)} calls for, so resting now is what keeps tomorrow "
                f"{_aday(tomorrow_now)}" + ("; if HRV dips overnight, it'll come down." if tomorrow_now in ("hard", "steady") else "."))
    if rx.verdict == eng.HARD:
        return f"That's the day. Tomorrow is {_aday(tomorrow_now)}: the fitness from today gets built while you recover."
    return (f"Nothing changes: today's session is done{' and done right' if score and score >= 75 else ''}. "
            f"Tomorrow is {_aday(tomorrow_now)}, and tonight's sleep is what decides whether it stays that way.")