"""Aerobic threshold (LT1) and aerobic efficiency, measured from steady sessions.

Mechanism: below your aerobic threshold, a steady effort holds the same
speed-per-heartbeat from the first half to the second. Above it, heart rate
drifts up at the same speed ("decoupling"). The highest steady heart rate you
can hold with under 5% drift is your aerobic threshold.

Only sessions where speed means something are used: runs and road rides.
Walks are excluded: walking HR sits far below the aerobic threshold, so "no drift
on a walk" says nothing about where the threshold is. Mountain biking and skiing
are excluded (terrain, not fitness, drives speed).

A threshold is only *measured* when it's bracketed: steady sessions below it held,
and at least one above it drifted. Without that, the best no-drift session is a
floor ("at least"), and the estimate falls back on the athlete's own LTHR.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .hr import clean

DRIFT_LIMIT = 5.0       # % decoupling: the textbook boundary for "aerobically steady"
MIN_MINUTES = 40
WARMUP_S = 10 * 60
STEADY_SD = 8.0         # bpm: an "interval" session isn't a steady session
MIN_SESSIONS = 3
SPEED_TYPES = {"Run", "VirtualRun", "Ride", "VirtualRide", "GravelRide",
               "running", "street_running", "treadmill_running", "trail_running", "road_biking"}
LT1_OF_LTHR = (0.80, 0.95)   # physiological bounds: LT1 sits ~80–95% of LTHR in trained athletes
LT1_DEFAULT = 0.89


@dataclass
class Steady:
    activity_id: str
    date: str
    sport: str
    avg_hr: float
    drift_pct: float
    ef: float            # metres per heartbeat (speed m/s ÷ HR bpm × 60)


def steady_session(activity_id: str, date: str, sport: str, raw_type: str | None,
                   time: list, hr: list, speed: list | None) -> Steady | None:
    if not speed or (raw_type and raw_type not in SPEED_TYPES):
        return None
    h = clean(time, hr)
    if len(h) < MIN_MINUTES * 60:
        return None
    t0 = np.asarray(time, float) - float(time[0])
    v = np.interp(np.arange(len(h)), t0, np.nan_to_num(np.asarray(
        [np.nan if x is None else x for x in speed], float), nan=0.0))
    h, v = h[WARMUP_S:], v[WARMUP_S:]
    moving = (v > 0.5) & ~np.isnan(h)
    if moving.sum() < 20 * 60 or np.nanstd(h[moving]) > STEADY_SD:
        return None
    half = len(h) // 2
    def ef(sl):
        m = moving[sl]
        return np.mean(v[sl][m]) / np.mean(h[sl][m]) * 60 if m.sum() > 300 else None
    e1, e2 = ef(slice(0, half)), ef(slice(half, None))
    if not e1 or not e2:
        return None
    # The HR you *held*: first half only, before any drift inflates it.
    first = moving[:half]
    return Steady(activity_id, date, sport, float(np.mean(h[:half][first])),
                  float((e1 - e2) / e1 * 100), float(np.mean(v[moving]) / np.mean(h[moving]) * 60))


@dataclass
class AerobicThreshold:
    bpm: int | None
    evidence: str
    sessions: list[Steady] = field(default_factory=list)


def estimate_lt1(sessions: list[Steady], lthr: float | None = None) -> AerobicThreshold:
    if lthr:
        # Sessions far below threshold can't locate LT1; they only add noise.
        sessions = [s for s in sessions if s.avg_hr >= LT1_OF_LTHR[0] * lthr - 8]
    est = _estimate(sessions)
    if not lthr:
        return est
    lo, hi = LT1_OF_LTHR[0] * lthr, LT1_OF_LTHR[1] * lthr
    default = round(LT1_DEFAULT * lthr)
    if est.bpm is None:
        return AerobicThreshold(default, f"Estimated at 89% of your threshold ({lthr:.0f}) until steady "
                                         f"runs or road rides measure it. {est.evidence}", sessions)
    if est.evidence.startswith("Floor"):
        if est.bpm >= default:
            return AerobicThreshold(min(round(hi), est.bpm), est.evidence.replace("Floor: ", ""), sessions)
        return AerobicThreshold(default, f"At least {est.bpm} (held without drift); estimated at 89% of "
                                         f"your threshold ({lthr:.0f}) until a session above it drifts.", sessions)
    if not lo <= est.bpm <= hi:
        return AerobicThreshold(default, f"Measured {est.bpm}, outside the plausible {lo:.0f}–{hi:.0f} for "
                                         f"your threshold of {lthr:.0f}; using 89% of threshold.", sessions)
    return est


def _estimate(sessions: list[Steady]) -> AerobicThreshold:
    if len(sessions) < MIN_SESSIONS:
        return AerobicThreshold(None, f"Need {MIN_SESSIONS} steady runs, walks or road rides "
                                      f"of {MIN_MINUTES}+ min ({len(sessions)} so far).", sessions)
    coupled = [s for s in sessions if s.drift_pct < DRIFT_LIMIT]
    drifted = [s for s in sessions if s.drift_pct >= DRIFT_LIMIT]
    if not coupled:
        return AerobicThreshold(None, "Every steady session drifted; need easier steady sessions.", sessions)
    # Highest HR held without drift, but not above an HR that did drift (noise guard).
    best = max(coupled, key=lambda s: s.avg_hr)
    lower_drift = [s for s in drifted if s.avg_hr < best.avg_hr - 3]
    if lower_drift:
        candidates = [s for s in coupled if s.avg_hr < min(d.avg_hr for d in lower_drift)]
        if candidates:
            best = max(candidates, key=lambda s: s.avg_hr)
    n_support = sum(1 for s in coupled if abs(s.avg_hr - best.avg_hr) <= 4)
    if not any(d.avg_hr > best.avg_hr for d in drifted):
        return AerobicThreshold(round(best.avg_hr),
                                f"Floor: held {best.avg_hr:.0f} bpm with {best.drift_pct:.1f}% drift on {best.date}; "
                                f"nothing harder has drifted yet, so the threshold is at least this.", sessions)
    return AerobicThreshold(round(best.avg_hr),
                            f"Held {best.avg_hr:.0f} bpm with {best.drift_pct:.1f}% drift on {best.date}; "
                            f"{n_support} session(s) agree, {len(drifted)} drifted above it.", sessions)
