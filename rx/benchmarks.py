"""You vs your age: each of your numbers against men 40–49 and endurance athletes 40–49.

Norms (see claude/RESEARCH.md for sources):
  VO2max   men 40–49: FRIEND 2022 treadmill (Kaminsky, Mayo Clin Proc) 10/25/50/75/90th =
           22.1 / 28 / 35.3 / 43 / 50.8.  Endurance athletes 40–49 (A Faster You cohort, n=385):
           42.1 / 48.3 / 54.3 / 60.2 / 65.2.
  Rest HR  men 40–49: Fitbit 64.6 mean, SD ~7.7 (Quer 2020). Trained endurance: ~45–55 overnight.
  HRV      men ~45: WHOOP middle 50% ~35–60 ms. Device-dependent; your own baseline matters more.
  Max HR   predicted 178 at 43 (Tanaka 2001); measured men 40–49 184 ± 11 (HUNT3). Not a fitness score.
  Sleep    adults 6.8 ± 0.8 h (Quer 2020); athletes need 7–9 h+ (consensus).
  Training 47% of US adults meet 150 min/week (CDC); masters endurance athletes ~8–15 h/week.
  Body fat men 40–49 Cooper percentiles (leaner = higher): 10/25/50/75/90/95th =
           29.2 / 25.7 / 21.9 / 18.4 / 14.9 / 12.8.  Endurance athletes ~6–13% (ACE).
VO2max estimates:
  Uth–Sørensen 2004: 15.3 × HRmax / HRrest (SEE ~4.5% with measured max). It assumes a
  seated morning resting HR; overnight wearable RHR runs ~6 bpm lower, so we add 6.
  ACSM cycling: 10.8 × (best 5-min power / kg) + 7.
  Garmin's own estimate, when intervals.icu passes it through.
"""
from __future__ import annotations

import json
import math
import os
import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np

from .analysis.hr import rolling_mean_full

AGE = int(os.getenv("RX_AGE", "43"))
ALT_FT = 6000


@dataclass
class Bench:
    key: str
    name: str
    value: float | None
    shown: str
    unit: str
    says: str
    pop: list[tuple[int, float]] = field(default_factory=list)       # (percentile, value)
    athlete: tuple[float, float] | None = None                       # typical range
    athlete_mid: float | None = None
    lower_is_better: bool = False
    neutral: bool = False
    note: str = ""
    lo: float = 0.0
    hi: float = 1.0


def _phi(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def percentile(x: float, pts: list[tuple[int, float]], lower_is_better=False) -> float:
    """Where x sits among the points; beyond the table, a normal fit from the 50th and the
    outermost point. Returns 0..100, 'higher than N% of men your age' (better side)."""
    pts = sorted(pts)
    vals = [v for _, v in pts]
    pcts = [p for p, _ in pts]
    inc = vals[-1] > vals[0]
    xs, ps = (vals, pcts) if inc else (vals[::-1], pcts[::-1])
    if xs[0] <= x <= xs[-1]:
        p = float(np.interp(x, xs, ps))
    else:
        p50 = float(np.interp(50, pcts, vals))
        far = pts[-1] if abs(x - pts[-1][1]) < abs(x - pts[0][1]) else pts[0]
        z_far = statistics.NormalDist().inv_cdf(far[0] / 100)
        sd = abs((far[1] - p50) / z_far) or 1
        z = (x - p50) / sd * (1 if inc else -1)
        p = 100 * _phi(z)
    return max(0.5, min(99.5, p))


def _latest_raw(store, key: str, days: int = 120):
    since = (date.today() - timedelta(days=days)).isoformat()
    for r in store.wellness_raw(since):
        try:
            raw = json.loads(r["raw"]) if r["raw"] else {}
        except ValueError:
            continue
        v = raw.get(key)
        if v:
            return float(v)
    return None


def vo2_estimates(store, p) -> tuple[list[tuple[str, float, str]], float | None, tuple | None]:
    out = []
    g = _latest_raw(store, "vo2max")
    if g:
        out.append(("Garmin's estimate", g, "From your watch, using pace and power against heart rate."))
    rhr = [w.rhr for w in p.wellness[-14:] if w.rhr]
    if p.max_hr.bpm and rhr:
        r = statistics.median(rhr) + 6
        v = 15.3 * p.max_hr.bpm / r
        out.append(("Max vs resting heart rate", v,
                    f"15.3 × your max {p.max_hr.bpm:.0f} ÷ resting {r:.0f} (overnight {r - 6:.0f} + 6). Uth–Sørensen; about ±4.5%."))
    wt = _latest_raw(store, "weight")
    if wt:
        best = 0.0
        cut = (date.fromisoformat(p.as_of) - timedelta(days=90)).isoformat()
        for a in p.activities:
            if a.sport != "bike" or a.date < cut:
                continue
            st = store.power(a.id)
            if st and len(st[1]) >= 300:
                m = rolling_mean_full(np.asarray([0 if x is None else x for x in st[1]], float), 300)
                if len(m):
                    best = max(best, float(np.nanmax(m)))
        if best:
            v = 10.8 * best / wt + 7
            out.append(("Best 5 minutes of power", v,
                        f"{best:.0f} W at {wt:.1f} kg = {best / wt:.2f} W/kg, last 90 days. ACSM cycling equation."))
    if not out:
        return out, None, None
    vals = [v for _, v, _ in out]
    return out, statistics.median(vals), (min(vals), max(vals))


def build(store, p) -> tuple[list[Bench], list, float | None, tuple | None]:
    B: list[Bench] = []
    est, vo2, vrange = vo2_estimates(store, p)

    if vo2:
        pc = percentile(vo2, [(10, 22.1), (25, 28), (50, 35.3), (75, 43), (90, 50.8)])
        pa = percentile(vo2, [(10, 42.1), (25, 48.3), (50, 54.3), (75, 60.2), (90, 65.2)])
        rng = f" (estimates range {vrange[0]:.0f}–{vrange[1]:.0f})" if vrange and vrange[1] - vrange[0] >= 2 else ""
        B.append(Bench("vo2", "VO2max", vo2, f"{vo2:.0f}", "ml/kg/min",
                       f"About {vo2:.0f}{rng}. Higher than {pc:.0f}% of men your age, and around the "
                       f"{pa:.0f}th percentile of endurance athletes your age.",
                       [(25, 28), (50, 35.3), (75, 43), (90, 50.8)], (48.3, 60.2), 54.3,
                       note=f"Living at {ALT_FT:,} ft, a lab test here would read roughly 9% lower than at sea level.",
                       lo=20, hi=75))

    rhr = [w.rhr for w in p.wellness[-14:] if w.rhr]
    if rhr:
        v = statistics.median(rhr)
        pts = [(10, 74.5), (25, 69.8), (50, 64.6), (75, 59.4), (90, 54.7)]
        pc = percentile(v, pts)
        B.append(Bench("rhr", "Resting heart rate", v, f"{v:.0f}", "bpm",
                       f"Lower than {pc:.0f}% of men your age (average is about 65). Trained endurance athletes "
                       f"usually sit in the mid 40s to low 50s overnight{', and so do you' if v <= 55 else ''}.",
                       [(25, 69.8), (50, 64.6), (75, 59.4), (90, 54.7)], (44, 54), 49, lower_is_better=True,
                       lo=35, hi=85))

    hv = [w.hrv for w in p.wellness[-7:] if w.hrv]
    if hv:
        v = statistics.mean(hv)
        pts = [(25, 35), (50, 45), (75, 60), (90, 75)]
        pc = percentile(v, pts)
        B.append(Bench("hrv", "HRV", v, f"{v:.0f}", "ms",
                       f"Higher than about {pc:.0f}% of men your age. Treat this one loosely: every device "
                       f"measures HRV differently, so your own trend matters far more than this ranking.",
                       pts, (50, 90), 65, lo=15, hi=120,
                       note="Population figures are WHOOP's for men around 45; Garmin reads somewhat differently."))

    if p.max_hr.bpm:
        v = p.max_hr.bpm
        pred = 208 - 0.7 * AGE
        B.append(Bench("max", "Max heart rate", v, f"{v:.0f}", "bpm",
                       f"Age formulas predict {pred:.0f}; men your age measure 184 ± 11. Yours is "
                       f"{'above' if v > pred + 3 else 'below' if v < pred - 3 else 'right at'} the formula. "
                       f"Max HR isn't a fitness score: it's why Ceiling uses your measured number, never the formula.",
                       [(25, 177), (50, 184), (75, 191)], None, None, neutral=True, lo=160, hi=205))

    sl = [w.sleep_secs / 3600 for w in p.wellness[-14:] if w.sleep_secs]
    if sl:
        v = statistics.mean(sl)
        pc = 100 * _phi((v - 6.8) / 0.8)
        h, m = int(v), int(round((v - int(v)) * 60))
        B.append(Bench("sleep", "Sleep", v, f"{h}h {m:02d}m", "a night",
                       f"More than {pc:.0f}% of adults. But you train like an athlete, and athletes need 8 hours "
                       f"or more to absorb it{': you are there' if v >= 8 else f': you are {int(round((8 - v) * 60))} minutes short of that'}.",
                       [(25, 6.26), (50, 6.8), (75, 7.34), (90, 7.83)], (7.5, 9.0), 8.25, lo=5, hi=10))

    end = date.fromisoformat(p.as_of)
    cut = (end - timedelta(days=28)).isoformat()
    hrs = sum(a.moving_s for a in p.activities if cut <= a.date < p.as_of) / 3600 / 4
    if hrs:
        B.append(Bench("hours", "Training", hrs, f"{hrs:.1f}", "hours a week",
                       f"{hrs / 2.5:.0f}× the 150-minute guideline that only about half of adults meet. Masters "
                       f"endurance athletes typically train 8–15 hours a week"
                       f"{'; you are in that range' if 8 <= hrs <= 15 else '; you are above it' if hrs > 15 else ''}.",
                       [(50, 1.5), (75, 3.0), (90, 5.0)], (8, 15), 11, lo=0, hi=20))

    bf = _latest_raw(store, "bodyFat")
    if bf:
        pts = [(10, 29.2), (25, 25.7), (50, 21.9), (75, 18.4), (90, 14.9), (95, 12.8)]
        pc = percentile(bf, pts)
        B.append(Bench("bf", "Body fat", bf, f"{bf:.1f}", "%",
                       f"Leaner than {pc:.0f}% of men your age (median about 22%). Endurance athletes run about 6–13%. "
                       f"Your scale uses bioimpedance, which can be off by a few points; watch its trend.",
                       [(25, 25.7), (50, 21.9), (75, 18.4), (90, 14.9)], (6, 13), 10, lower_is_better=True,
                       lo=5, hi=35))
    return B, est, vo2, vrange


def scale(b: Bench, w: int = 1000, h: int = 92) -> str:
    """One axis. Grey band = middle half of men your age, tick = average. Green band = endurance
    athletes your age. Black dot = you."""
    X = lambda v: 20 + (w - 40) * (max(b.lo, min(b.hi, v)) - b.lo) / (b.hi - b.lo)
    o = [f'<svg class="bscale" viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="{b.name}: you compared with men your age and athletes your age">']
    o.append(f'<line class="axis" x1="20" x2="{w - 20}" y1="46" y2="46"/>')
    pts = dict(b.pop)
    if 25 in pts and 75 in pts:
        a, c = sorted((X(pts[25]), X(pts[75])))
        o.append(f'<rect class="popb" x="{a:.1f}" y="38" width="{c - a:.1f}" height="16" rx="3"/>')
    if 50 in pts:
        o.append(f'<line class="popm" x1="{X(pts[50]):.1f}" x2="{X(pts[50]):.1f}" y1="32" y2="60"/>')
        o.append(f'<text class="lbl" x="{X(pts[50]):.1f}" y="80" text-anchor="middle">average man {AGE}</text>')
    if b.athlete:
        a, c = sorted((X(b.athlete[0]), X(b.athlete[1])))
        o.append(f'<rect class="athb" x="{a:.1f}" y="38" width="{c - a:.1f}" height="16" rx="3"/>')
        o.append(f'<text class="lbl ath" x="{(a + c) / 2:.1f}" y="80" text-anchor="middle">endurance athletes</text>')
    if b.value is not None:
        x = X(b.value)
        o.append(f'<circle class="you" cx="{x:.1f}" cy="46" r="11"/>')
        o.append(f'<text class="youl" x="{x:.1f}" y="22" text-anchor="middle">you {b.shown}</text>')
    o.append("</svg>")
    return "".join(o)
