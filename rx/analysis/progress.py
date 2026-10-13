"""Am I progressing? Each measure compares the last few weeks to ~2 months earlier.

Every measure is something only fitness changes, judged against your own past:
- Aerobic efficiency: speed per heartbeat on steady sessions (faster at the same HR).
- Resting HR: lower is fitter (and recovered).
- HRV: higher is fitter (and recovered).
- Heart-rate recovery: how far HR falls in the 60 s after a hard effort.
- Fitness: 6-week training load.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np

from .aerobic import Steady
from .hr import clean

UP, FLAT, DOWN, NA = "improving", "flat", "declining", "not enough data"


@dataclass
class Measure:
    name: str
    now: float | None
    then: float | None
    trend: str
    sentence: str


@dataclass
class Progress:
    headline: str
    measures: list[Measure]


def _window(items, key_date, start: date, end: date):
    return [v for d, v in items if start.isoformat() <= d <= end.isoformat()]


def _trend(change: float, threshold: float, higher_is_better: bool = True) -> str:
    if abs(change) < threshold:
        return FLAT
    return UP if (change > 0) == higher_is_better else DOWN


def hr_recovery(time: list, hr: list, lthr: float) -> float | None:
    """HR drop in the 60 s after the hardest 3-minute block, if that block ended hard."""
    h = clean(time, hr)
    if len(h) < 600:
        return None
    w = 180
    win = np.lib.stride_tricks.sliding_window_view(h, w)
    m = win.mean(axis=1)
    m[np.isnan(win).any(axis=1)] = np.nan
    if np.all(np.isnan(m)):
        return None
    i = int(np.nanargmax(m))
    end = i + w
    if end + 60 >= len(h) or np.isnan(h[end - 1]) or np.isnan(h[end + 59]):
        return None
    if h[end - 1] < 0.95 * lthr:
        return None
    drop = h[end - 1] - h[end + 59]
    return float(drop) if drop > 5 else None   # no drop means the effort didn't stop


def build(as_of: str, steady: list[Steady], wellness, load_days, hrr: list[tuple[str, float]],
          lt1: int | None) -> Progress:
    today = date.fromisoformat(as_of)
    recent = (today - timedelta(days=42), today)
    prior = (today - timedelta(days=126), today - timedelta(days=43))
    out: list[Measure] = []

    # 1. Aerobic efficiency, per sport, on undrifted steady sessions at/below LT1
    for sport in sorted({s.sport for s in steady}):
        ss = [(s.date, s.ef) for s in steady if s.sport == sport and s.drift_pct < 5
              and (lt1 is None or s.avg_hr <= lt1 + 3)]
        a, b = _window(ss, 0, *recent), _window(ss, 0, *prior)
        if len(a) >= 2 and len(b) >= 2:
            ch = (statistics.mean(a) / statistics.mean(b) - 1) * 100
            t = _trend(ch, 2.0)
            word = "faster" if ch >= 0 else "slower"
            out.append(Measure(f"Aerobic efficiency ({sport})", statistics.mean(a), statistics.mean(b), t,
                               f"At the same easy heart rate you're {abs(ch):.1f}% {word} than 6–18 weeks ago."))
        else:
            out.append(Measure(f"Aerobic efficiency ({sport})", None, None, NA,
                               "Needs 2+ steady sessions in each period to compare."))

    # 2. Resting HR and 3. HRV
    rhr = [(w.date, w.rhr) for w in wellness if w.rhr]
    hrv = [(w.date, w.hrv) for w in wellness if w.hrv]
    r_now = _window(rhr, 0, today - timedelta(days=14), today)
    r_then = _window(rhr, 0, today - timedelta(days=70), today - timedelta(days=56))
    if len(r_now) >= 7 and len(r_then) >= 7:
        a, b = statistics.median(r_now), statistics.median(r_then)
        out.append(Measure("Resting HR", a, b, _trend(a - b, 2.0, higher_is_better=False),
                           f"{a:.0f} bpm now vs {b:.0f} eight weeks ago."))
    else:
        out.append(Measure("Resting HR", None, None, NA, "Needs 8+ weeks of resting HR."))
    h_now = _window(hrv, 0, today - timedelta(days=14), today)
    h_then = _window(hrv, 0, today - timedelta(days=70), today - timedelta(days=56))
    if len(h_now) >= 7 and len(h_then) >= 7:
        a, b = statistics.mean(h_now), statistics.mean(h_then)
        out.append(Measure("HRV", a, b, _trend((a / b - 1) * 100, 5.0),
                           f"{a:.0f} ms now vs {b:.0f} eight weeks ago."))
    else:
        out.append(Measure("HRV", None, None, NA, "Needs 8+ weeks of overnight HRV."))

    # 4. Heart-rate recovery
    a, b = _window(hrr, 0, *recent), _window(hrr, 0, *prior)
    if len(a) >= 2 and len(b) >= 2:
        x, y = statistics.median(a), statistics.median(b)
        out.append(Measure("Heart-rate recovery", x, y, _trend(x - y, 3.0),
                           f"HR falls {x:.0f} bpm in the minute after a hard effort, vs {y:.0f} before."))
    else:
        out.append(Measure("Heart-rate recovery", None, None, NA, "Needs more hard efforts that end cleanly."))

    # 5. Fitness
    by = {ld.date: ld for ld in load_days}
    if load_days:
        nowd = load_days[-1]
        then = by.get((date.fromisoformat(nowd.date) - timedelta(days=42)).isoformat())
        if then and then.ctl > 1:
            ch = (nowd.ctl / then.ctl - 1) * 100
            out.append(Measure("Fitness (6-week load)", nowd.ctl, then.ctl, _trend(ch, 10.0),
                               f"{nowd.ctl:.0f} now vs {then.ctl:.0f} six weeks ago ({ch:+.0f}%)."))

    scored = [m for m in out if m.trend != NA]
    ups = sum(m.trend == UP for m in scored)
    downs = sum(m.trend == DOWN for m in scored)
    if not scored:
        headline = "Not enough history yet."
    elif ups > downs and ups >= 2:
        headline = "You're progressing."
    elif downs > ups:
        headline = "You're going backward."
    else:
        headline = "Holding steady."
    return Progress(headline, out)
