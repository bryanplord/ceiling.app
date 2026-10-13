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
    """Each of the last few days: what the plan said the evening before, what the morning made of it, and why."""
    from datetime import date as _d
    from .prescribe import plan as planmod
    end = _d.fromisoformat(local_today().isoformat())
    out = []
    for k in range(days - 1, -1, -1):
        d = (end - timedelta(days=k)).isoformat()
        y = (end - timedelta(days=k + 1)).isoformat()
        try:
            p, rx = today_rx(store, d)
            py, rxy = today_rx(store, y)
            prev = planmod.build(py, rxy)
            planned = next((x for x in prev.days if x.date == d), None)
            if planned is None or planned.reason.startswith("You rarely"):
                continue
            from .prescribe.engine import ORDER
            why = planmod.causes(p, rx, prev)
            if planned.verdict == rx.verdict:
                text = "Went as planned."
            else:
                up = 1 if ORDER.index(rx.verdict) > ORDER.index(planned.verdict) else -1
                text = planmod.explain(up, why, rx)
            out.append({"date": d, "was": planned.verdict, "now": rx.verdict, "why": text,
                        "changed": planned.verdict != rx.verdict})
        except Exception:
            continue
    return out
