"""Training load: Banister TRIMP per session, then fatigue / fitness / form.

fatigue (ATL) = 7-day exponential average of daily load
fitness (CTL) = 42-day exponential average
form   (TSB)  = fitness - fatigue (negative = carrying fatigue)
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np

from .hr import clean

ATL_DAYS = 7
CTL_DAYS = 42


def trimp(time: list, hr: list, rest: float, max_hr: float) -> float:
    h = clean(time, hr)
    h = h[~np.isnan(h)]
    if len(h) == 0 or max_hr <= rest:
        return 0.0
    r = np.clip((h - rest) / (max_hr - rest), 0, 1)
    return float(np.sum(r * 0.64 * np.exp(1.92 * r)) / 60.0)


def trimp_from_summary(minutes: float, avg_hr: float | None, rest: float, max_hr: float) -> float:
    """Fallback when there's no stream (e.g. imported summary only)."""
    if not avg_hr or max_hr <= rest:
        return 0.0
    r = max(0.0, min(1.0, (avg_hr - rest) / (max_hr - rest)))
    return minutes * r * 0.64 * math.exp(1.92 * r)


@dataclass
class LoadDay:
    date: str
    load: float
    atl: float
    ctl: float

    @property
    def tsb(self) -> float:
        return self.ctl - self.atl

    @property
    def ratio(self) -> float:
        return self.atl / self.ctl if self.ctl > 1 else 1.0


def series(daily_load: dict[str, float], end: str | None = None, start: str | None = None) -> list[LoadDay]:
    if not daily_load and not end:
        return []
    days = sorted(daily_load)
    d0 = date.fromisoformat(start or days[0])
    d1 = date.fromisoformat(end or days[-1])
    ka, kc = 1 - math.exp(-1 / ATL_DAYS), 1 - math.exp(-1 / CTL_DAYS)
    atl = ctl = 0.0
    out = []
    d = d0
    while d <= d1:
        load = daily_load.get(d.isoformat(), 0.0)
        atl += (load - atl) * ka
        ctl += (load - ctl) * kc
        out.append(LoadDay(d.isoformat(), load, atl, ctl))
        d += timedelta(days=1)
    return out


def step(prev: LoadDay, load: float, day: str) -> LoadDay:
    """Project one day forward: used by the if/then scenarios."""
    ka, kc = 1 - math.exp(-1 / ATL_DAYS), 1 - math.exp(-1 / CTL_DAYS)
    atl = prev.atl + (load - prev.atl) * ka
    ctl = prev.ctl + (load - prev.ctl) * kc
    return LoadDay(day, load, atl, ctl)
