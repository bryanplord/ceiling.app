"""How YOUR body responds to load, learned from your own history.

For every day: yesterday's load relative to your fitness, against this morning's
HRV relative to your baseline. A straight-line fit gives your personal cost of a
hard day. Separately: after big days, how many mornings until HRV is back.
This replaces generic rules with your measured response as data accumulates.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np

from ..store import Wellness
from .load import LoadDay

MIN_PAIRS = 20
BIG_DAY_PCTL = 85       # a "big day" is in your top 15% of training days by relative load


@dataclass
class Response:
    pairs: int
    intercept: float | None
    slope: float | None          # HRV z-score per unit of relative load
    sd: float | None             # your typical day-to-day HRV spread (log units)
    recovery_days: float | None  # median mornings to get back to baseline after big days
    big_days: int

    @property
    def fitted(self) -> bool:
        return self.slope is not None

    def hrv_change_pct(self, rel_load: float) -> float | None:
        if not self.fitted:
            return None
        z = self.intercept + self.slope * rel_load
        return (math.exp(z * self.sd) - 1) * 100


def _z_by_date(wellness: list[Wellness]) -> tuple[dict[str, float], list[float]]:
    days = [w for w in wellness if w.hrv and w.hrv > 0]
    out, sds = {}, []
    for i, w in enumerate(days):
        hist = [math.log(x.hrv) for x in days[max(0, i - 60):i]]
        if len(hist) < 14:
            continue
        mu, sd = statistics.mean(hist), statistics.pstdev(hist) or 0.05
        out[w.date] = (math.log(w.hrv) - mu) / sd
        sds.append(sd)
    return out, sds


def fit(wellness: list[Wellness], load_days: list[LoadDay]) -> Response:
    z, sds = _z_by_date(wellness)
    by_day = {ld.date: ld for ld in load_days}
    xs, ys, mornings = [], [], []
    for d, zval in z.items():
        y = (date.fromisoformat(d) - timedelta(days=1)).isoformat()
        yy = (date.fromisoformat(d) - timedelta(days=2)).isoformat()
        if y in by_day and yy in by_day and by_day[yy].ctl > 5:
            rel = by_day[y].load / by_day[yy].ctl
            xs.append(rel)
            ys.append(zval)
            mornings.append(d)
    trained = [x for x in xs if x > 0]
    cut = float(np.percentile(trained, BIG_DAY_PCTL)) if len(trained) >= 10 else float("inf")
    big = [d for d, x in zip(mornings, xs) if x >= cut]

    rec_days = []
    for d in big:   # d is the first morning after the big day; k = extra mornings needed
        for k in range(0, 8):
            dk = (date.fromisoformat(d) + timedelta(days=k)).isoformat()
            if dk in z and z[dk] >= -0.5:
                rec_days.append(k)
                break
    rd = float(statistics.median(rec_days)) if rec_days else None

    if len(xs) < MIN_PAIRS or np.std(xs) < 0.1:
        return Response(len(xs), None, None, None, rd, len(big))
    b, a = np.polyfit(xs, ys, 1)
    return Response(len(xs), float(a), float(b), float(statistics.mean(sds)), rd, len(big))
