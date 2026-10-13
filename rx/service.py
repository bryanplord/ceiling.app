"""Shared glue used by both the CLI and the web app."""
from __future__ import annotations

from datetime import date, timedelta

from . import profile as prof
from .analysis.recovery import assess
from .config import settings
from .prescribe.engine import build_rx
from .prescribe.score import score
from .store import Store


def open_store() -> Store:
    return Store(settings().db_path)


def today_rx(store: Store, day: str | None = None):
    day = day or date.today().isoformat()
    p = prof.build(store, day)
    return p, build_rx(p, day)


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
    end = date.today()
    return [assess(ws, (end - timedelta(days=i)).isoformat()) for i in range(days - 1, -1, -1)]
