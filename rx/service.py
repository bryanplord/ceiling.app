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


def open_store() -> Store:
    """Plain SQLite: a fresh connection each time. Turso: one shared replica per
    process (opening one means pulling from Turso, too slow to do per request)."""
    global _shared
    s = settings()
    if not s.turso_url:
        return Store(s.db_path)
    if _shared is None:
        _shared = Store(s.data_dir / "replica.db", s.turso_url, s.turso_token)
    return _shared


_built: dict = {}
_build_lock = threading.RLock()


def data_version(store: Store) -> tuple:
    """Changes whenever a sync brings anything in; cheap to read."""
    c = store.counts()
    return (c["activities"], c["streams"], c["wellness"], store.get_meta("last_sync") or "")


def today_rx(store: Store, day: str | None = None):
    """Profile + prescription for a day, rebuilt only when the data changes.
    Building means reading every heart-rate stream, so it must not run per request."""
    day = day or local_today().isoformat()
    key = (day, data_version(store))
    with _build_lock:
        hit = _built.get(key)
        if hit is None:
            p = prof.build(store, day)
            hit = (p, build_rx(p, day))
            if len(_built) > 8:
                _built.clear()
            _built[key] = hit
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
            if planned and not planned.reason.startswith("You rarely") and planned.verdict != rx.verdict:
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
