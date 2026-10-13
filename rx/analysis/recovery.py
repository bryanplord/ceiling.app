"""Recovery, always against the athlete's own baseline.

HRV: ln(rMSSD). Baseline = mean and SD over the prior 60 days (excluding today).
     Today, and the 7-day rolling mean, are judged against that band.
Resting HR: today vs 60-day median.
Sleep: score vs personal median, plus an absolute floor.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field

from ..store import Wellness

GREEN, AMBER, RED, UNKNOWN = "green", "amber", "red", "unknown"
BASELINE_DAYS = 60
MIN_BASELINE = 14


@dataclass
class Signal:
    name: str
    status: str
    today: float | None
    baseline: float | None
    detail: str


@dataclass
class Recovery:
    date: str
    hrv: Signal
    rhr: Signal
    sleep: Signal
    signals: list[Signal] = field(default_factory=list)

    def __post_init__(self):
        self.signals = [self.hrv, self.rhr, self.sleep]

    @property
    def reds(self) -> int:
        return sum(s.status == RED for s in self.signals)

    @property
    def ambers(self) -> int:
        return sum(s.status == AMBER for s in self.signals)

    @property
    def known(self) -> int:
        return sum(s.status != UNKNOWN for s in self.signals)


def _hrv(history: list[Wellness], today: Wellness) -> Signal:
    vals = [math.log(w.hrv) for w in history if w.hrv and w.hrv > 0]
    if today.hrv is None or len(vals) < MIN_BASELINE:
        return Signal("HRV", UNKNOWN, today.hrv, None,
                      f"Need {MIN_BASELINE} nights of HRV for a baseline ({len(vals)} so far).")
    mu, sd = statistics.mean(vals), statistics.pstdev(vals) or 0.05
    z = (math.log(today.hrv) - mu) / sd
    base = round(math.exp(mu))
    recent = [math.log(w.hrv) for w in history[-6:] if w.hrv and w.hrv > 0] + [math.log(today.hrv)]
    z7 = (statistics.mean(recent) - mu) / sd
    if z < -1.5 or (z < -1.0 and z7 < -0.5):
        st = RED
    elif z < -0.75 or z7 < -0.75:
        st = AMBER
    else:
        st = GREEN
    pct = round((today.hrv / base - 1) * 100)
    return Signal("HRV", st, today.hrv, base, f"{today.hrv:.0f} ms vs your normal {base} ms ({pct:+d}%).")


def _rhr(history: list[Wellness], today: Wellness) -> Signal:
    vals = [w.rhr for w in history if w.rhr]
    if today.rhr is None or len(vals) < MIN_BASELINE:
        return Signal("Resting HR", UNKNOWN, today.rhr, None, "Not enough resting HR history yet.")
    base = statistics.median(vals)
    d = today.rhr - base
    st = RED if d >= 6 else AMBER if d >= 3 else GREEN
    return Signal("Resting HR", st, today.rhr, base, f"{today.rhr:.0f} bpm vs your normal {base:.0f} ({d:+.0f}).")


def _sleep(history: list[Wellness], today: Wellness) -> Signal:
    if today.sleep_score is None:
        if today.sleep_secs:
            h = today.sleep_secs / 3600
            st = RED if h < 5.5 else AMBER if h < 6.75 else GREEN
            return Signal("Sleep", st, round(h, 1), None, f"{h:.1f} hours.")
        return Signal("Sleep", UNKNOWN, None, None, "No sleep data for last night.")
    vals = [w.sleep_score for w in history if w.sleep_score]
    base = statistics.median(vals) if len(vals) >= 7 else None
    s = today.sleep_score
    if s < 50 or (base and s < base - 20):
        st = RED
    elif s < 65 or (base and s < base - 10):
        st = AMBER
    else:
        st = GREEN
    detail = f"Score {s:.0f}" + (f" vs your normal {base:.0f}." if base else ".")
    return Signal("Sleep", st, s, base, detail)


def assess(wellness: list[Wellness], day: str) -> Recovery:
    by_date = {w.date: w for w in wellness}
    today = by_date.get(day, Wellness(date=day))
    history = [w for w in wellness if w.date < day][-BASELINE_DAYS:]
    return Recovery(day, _hrv(history, today), _rhr(history, today), _sleep(history, today))


def resting_hr_baseline(wellness: list[Wellness], default: float = 50.0) -> float:
    vals = [w.rhr for w in wellness[-BASELINE_DAYS:] if w.rhr]
    return float(statistics.median(vals)) if vals else default
