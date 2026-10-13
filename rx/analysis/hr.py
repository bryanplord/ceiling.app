"""Heart-rate physiology from the athlete's own streams.

No age formulas, no device zones. Everything here is derived from recorded data.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

HR_MIN, HR_MAX = 35, 230
SPIKE_BPM = 15          # a sample this far from its 11-second median is a glitch
PEAK_WINDOW_S = 15      # a max must be held this long to count
MAX_GAP_S = 5           # gaps longer than this are not interpolated


def clean(time: list, hr: list) -> np.ndarray:
    """Return 1 Hz heart rate with glitches and long gaps set to NaN."""
    t = np.asarray(time, dtype=float)
    h = np.asarray([np.nan if v is None else v for v in hr], dtype=float)
    if len(t) == 0 or len(t) != len(h):
        return np.array([])
    h[(h < HR_MIN) | (h > HR_MAX)] = np.nan
    t = t - t[0]
    grid = np.arange(0, int(t[-1]) + 1)
    ok = ~np.isnan(h)
    if ok.sum() < 2:
        return np.full(len(grid), np.nan)
    out = np.interp(grid, t[ok], h[ok])
    # blank out samples that fall inside long recording gaps
    tk = t[ok]
    idx = np.searchsorted(tk, grid)
    prev = tk[np.clip(idx - 1, 0, len(tk) - 1)]
    nxt = tk[np.clip(idx, 0, len(tk) - 1)]
    out[(nxt - prev) > MAX_GAP_S] = np.nan
    # spike filter against a centered rolling median
    med = _rolling(out, 11, np.nanmedian)
    out[np.abs(out - med) > SPIKE_BPM] = np.nan
    return out


def _rolling(x: np.ndarray, w: int, fn) -> np.ndarray:
    if len(x) < w:
        return np.full(len(x), np.nan)
    pad = w // 2
    xp = np.pad(x, pad, constant_values=np.nan)
    win = np.lib.stride_tricks.sliding_window_view(xp, w)[: len(x)]
    with np.errstate(all="ignore"):
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            return fn(win, axis=1)


def rolling_mean_full(x: np.ndarray, w: int) -> np.ndarray:
    """Trailing mean over w seconds; NaN unless the whole window has data."""
    if len(x) < w:
        return np.array([])
    win = np.lib.stride_tricks.sliding_window_view(x, w)
    m = win.mean(axis=1)
    m[np.isnan(win).any(axis=1)] = np.nan
    return m


@dataclass
class Peak:
    activity_id: str
    date: str
    sport: str
    bpm: float
    credible: bool
    reason: str = ""


def credible_peak(activity_id: str, date: str, sport: str, time: list, hr: list,
                  cadence: list | None = None) -> Peak | None:
    h = clean(time, hr)
    m = rolling_mean_full(h, PEAK_WINDOW_S)
    if len(m) == 0 or np.all(np.isnan(m)):
        return None
    i = int(np.nanargmax(m))
    bpm = float(m[i])
    seg = h[i : i + PEAK_WINDOW_S]

    # 1. Smooth ramp: real maxima are climbed to; glitches jump.
    lead = h[max(0, i - 120) : i]
    lead = lead[~np.isnan(lead)]
    if len(lead) < 30:
        return Peak(activity_id, date, sport, bpm, False, "no lead-in data")
    if np.nanmax(lead) < bpm - 25 and np.nanmean(lead[-30:]) < bpm - 20:
        return Peak(activity_id, date, sport, bpm, False, "jumped, no ramp")

    # 2. Cadence lock: optical sensors sometimes report step or pedal rate.
    if cadence is not None and len(cadence) == len(time):
        c = np.asarray([np.nan if v is None else v for v in cadence], dtype=float)
        t0 = np.asarray(time, float) - float(time[0])
        cg = np.interp(np.arange(len(h)), t0, np.nan_to_num(c, nan=0.0))
        cseg = cg[i : i + PEAK_WINDOW_S]
        for mult in (1, 2):
            if np.nanmedian(np.abs(seg - cseg * mult)) < 3:
                return Peak(activity_id, date, sport, bpm, False, "matches cadence")
    return Peak(activity_id, date, sport, bpm, True)


@dataclass
class MaxHR:
    bpm: float | None
    confirmed: bool
    note: str
    top: list[Peak] = field(default_factory=list)
    rejected: list[Peak] = field(default_factory=list)


def estimate_max_hr(peaks: list[Peak]) -> MaxHR:
    good = sorted([p for p in peaks if p.credible], key=lambda p: -p.bpm)
    bad = sorted([p for p in peaks if not p.credible], key=lambda p: -p.bpm)
    if not good:
        return MaxHR(None, False, "No credible hard efforts yet.", [], bad[:5])
    top = good[0]
    others = [p for p in good[1:] if p.date != top.date]
    if others and top.bpm - others[0].bpm <= 4:
        return MaxHR(round(top.bpm), True,
                     f"Reached on {top.date} and within 4 bpm on {others[0].date}.", good[:5], bad[:5])
    note = f"Single highest effort on {top.date}; not yet matched on another day."
    return MaxHR(round(top.bpm), False, note, good[:5], bad[:5])


@dataclass
class Threshold:
    bpm: float | None
    source: str


def best_sustained(time: list, hr: list, seconds: int) -> float | None:
    h = clean(time, hr)
    m = rolling_mean_full(h, seconds)
    if len(m) == 0 or np.all(np.isnan(m)):
        return None
    return float(np.nanmax(m))


def estimate_threshold(efforts: list[tuple[str, float | None, float | None]]) -> Threshold:
    """efforts: (date, best 20-min mean HR, best 60-min mean HR) per activity.

    LTHR ~ what can be held for an hour. A hard 20-min effort runs ~5% above it.
    Uncontrolled riding underestimates both, so this is a floor that rises as
    harder efforts appear. Refine later with HR-vs-speed decoupling.
    """
    best, src = None, "not enough sustained efforts yet"
    for d, b20, b60 in efforts:
        for est, label in ((b60, "60-min"), (b20 * 0.95 if b20 else None, "20-min × 0.95")):
            if est and (best is None or est > best):
                best, src = est, f"{label} effort on {d}"
    return Threshold(round(best) if best else None, src)


@dataclass
class Zones:
    lthr: int
    bounds: list[tuple[str, int, int]]
    measured: bool = False
    basis: str = ""

    def top(self, name: str) -> int:
        return next(hi for n, lo, hi in self.bounds if n == name)

    def bottom(self, name: str) -> int:
        return next(lo for n, lo, hi in self.bounds if n == name)


def zones_from_anchors(lt1: int, lthr: int, max_hr: int | None) -> Zones:
    """Zones from two measured points: aerobic threshold (LT1) and threshold (LT2).

    Z2 tops out exactly at your measured LT1, Z4 exactly at your threshold.
    The splits inside each band are halfway points between your own anchors.
    """
    cap = max_hr or round(lthr * 1.1)
    gap = max(6, lthr - lt1)
    z1_top = lt1 - round(gap / 2)
    mid = lt1 + round(gap / 2)
    z = [
        ("Z1 Recovery", 0, z1_top),
        ("Z2 Aerobic", z1_top + 1, lt1),
        ("Z3 Tempo", lt1 + 1, mid),
        ("Z4 Threshold", mid + 1, lthr),
        ("Z5 Max", lthr + 1, cap),
    ]
    return Zones(lthr, z, measured=True,
                 basis=f"Measured: aerobic threshold {lt1}, threshold {lthr}, max {cap}.")


def zones_from_lthr(lthr: int, max_hr: int | None) -> Zones:
    """PROVISIONAL: used only until an aerobic threshold has been measured.
    Percentages of the athlete's own threshold, not of a formula."""
    cap = max_hr or round(lthr * 1.1)
    z = [
        ("Z1 Recovery", 0, round(lthr * 0.81)),
        ("Z2 Aerobic", round(lthr * 0.81) + 1, round(lthr * 0.89)),
        ("Z3 Tempo", round(lthr * 0.89) + 1, round(lthr * 0.93)),
        ("Z4 Threshold", round(lthr * 0.93) + 1, round(lthr * 1.00)),
        ("Z5 Max", round(lthr * 1.00) + 1, cap),
    ]
    return Zones(lthr, z, measured=False,
                 basis="Provisional: aerobic threshold not measured yet, so bands below threshold "
                       "are estimated. They're replaced as soon as steady sessions measure it.")
