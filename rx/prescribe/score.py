"""How well did a session follow the prescription?"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..analysis.hr import clean
from .engine import Rx


@dataclass
class Score:
    score: int
    minutes: int
    minutes_over: int
    minutes_in_range: int
    pct_in_range: int
    peak: int | None
    over_duration: int
    summary: str
    cost: str


def score(rx: Rx, time: list, hr: list, sport: str | None = None) -> Score:
    h = clean(time, hr)
    valid = h[~np.isnan(h)]
    minutes = round(len(h) / 60)
    if len(valid) == 0 or rx.hr_ceiling is None:
        return Score(0, minutes, 0, 0, 0, None, 0, "No heart-rate data to score.", "")

    if sport in ("bike", "ski") and len(h) >= 60:
        # Intermittent terrain sports: count sustained time over the ceiling (60-s average),
        # so a 15-second spike on a steep pitch isn't scored like a minute of threshold work.
        from ..analysis.hr import rolling_mean_full
        sustained = rolling_mean_full(np.where(np.isnan(h), np.nanmean(valid), h), 60)
        over = int(np.sum(sustained > rx.hr_ceiling) / 60)
    else:
        over = int(np.sum(valid > rx.hr_ceiling) / 60)
    lo = rx.hr_low if rx.hr_low is not None else 0
    in_rng = int(np.sum((valid >= lo) & (valid <= rx.hr_ceiling)) / 60)
    pct = round(100 * in_rng / max(1, len(valid) / 60))
    over_dur = max(0, minutes - (rx.max_minutes or minutes))
    peak = int(np.max(valid))

    s = 100
    s -= min(50, over * 3)          # ceiling breaches cost the most
    s -= min(30, over_dur * 1)       # going long
    if rx.verdict in ("steady", "hard"):
        s -= min(20, round((100 - pct) * 0.2))  # drifting below range matters on work days
    s = max(0, s)

    parts = [f"{minutes} min"]
    if rx.max_minutes and over_dur:
        parts.append(f"{over_dur} min over the {rx.max_minutes}-min cap")
    if over:
        parts.append(f"{over} min above {rx.hr_ceiling}, peak {peak}")
    else:
        parts.append(f"never above {rx.hr_ceiling}")
    summary = ", ".join(parts) + "."
    if sport and sport != rx.sport:
        summary += f" Prescribed a {rx.sport} session; this was a {sport}."
    if s >= 90:
        cost = "Done as prescribed."
    elif over >= 10 or over_dur >= 20:
        cost = "Expect it to show in tomorrow's recovery. If HRV drops, tomorrow becomes easy."
    elif rx.verdict in ("steady", "hard") and pct < 50:
        cost = "Easier than prescribed. No recovery cost, but little fitness gained."
    else:
        cost = "Close. Minor cost."
    return Score(s, minutes, over, in_rng, pct, peak, over_dur, summary, cost)
