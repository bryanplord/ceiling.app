"""Consistency checks on the raw record, so that readings the data itself
contradicts are left out before anything is computed from them.

Each check compares one stream with another stream of the same session, or a
reading with the readings around it. None compares the person with anyone
else. A reading that fails a check is left out (set to missing) and the
session's notes name what was left out and why, so the coach can see it and
weigh it.

Checks on heart rate, in order:
  spike      a reading more than SPIKE_BPM from the median of the eleven
             seconds around it
  stuck      the same reading repeated for STUCK_S seconds or more while
             moving (a sensor that stopped updating)
  cadence    on foot at running cadence, heart rate within LOCK_BPM of the step
             rate (or the stride rate) for LOCK_S seconds or more, entered with
             a jump of LOCK_JUMP_BPM while the pace stayed the same: the optical
             sensor reporting arm swing instead of the pulse (a match reached
             with the pace rising is kept and named as doubtful)
Checks on one-minute peaks (see credible_peak):
  ramp       a peak with no rise into it: the two minutes before it never came
             within RAMP_BPM of it
"""
from __future__ import annotations

import math

import numpy as np

SPIKE_BPM = 15      # bpm from the median of the 11 seconds around a reading
STUCK_S = 180       # seconds of an unchanged reading while moving
STUCK_N = 20        # ... over at least this many readings
LOCK_BPM = 3        # median gap between heart rate and step (or stride) rate
LOCK_S = 120        # ... held for at least this long
LOCK_SPM = 140      # only at running cadence, steps per minute
LOCK_JUMP_BPM = 10  # a lock is entered with a jump of at least this much over the two minutes before
RAMP_S = 120        # the lead-in looked at before a one-minute peak
RAMP_BPM = 25       # a peak the lead-in never came within this far of has no ramp
MAX_GAP_S = 5       # readings further apart than this are not joined for checking


def _grid(t, x):
    """x on a one-second grid, missing across gaps longer than MAX_GAP_S."""
    g = np.arange(0, int(t[-1] - t[0]) + 1, dtype=float)
    tt = t - t[0]
    f = np.isfinite(x)
    if f.sum() < 2:
        return g, np.full(len(g), np.nan)
    out = np.interp(g, tt[f], x[f])
    tk = tt[f]
    j = np.searchsorted(tk, g)
    prev = tk[np.clip(j - 1, 0, len(tk) - 1)]
    nxt = tk[np.clip(j, 0, len(tk) - 1)]
    out[(nxt - prev) > MAX_GAP_S] = np.nan
    return g, out


def _rolling_median(x, w):
    """Centred rolling median over w samples, ignoring missing values."""
    n = len(x)
    if n < w:
        return np.full(n, np.nan)
    pad = w // 2
    xp = np.pad(x, pad, constant_values=np.nan)
    win = np.sort(np.lib.stride_tricks.sliding_window_view(xp, w)[:n], axis=1)
    k = (~np.isnan(win)).sum(axis=1)
    rows = np.arange(n)
    lo = win[rows, np.maximum((k - 1) // 2, 0)]
    hi = win[rows, np.maximum(k // 2, 0)]
    med = (lo + hi) / 2
    med[k < max(3, w // 3)] = np.nan
    return med


def _at(g_vals, t):
    """Grid values at the raw timestamps."""
    i = np.clip(np.round(t - t[0]).astype(int), 0, len(g_vals) - 1)
    return g_vals[i]


def _runs(mask):
    """(start, end) index pairs of runs of True."""
    if not mask.any():
        return []
    d = np.diff(np.concatenate(([0], mask.astype(int), [0])))
    return list(zip(np.nonzero(d == 1)[0], np.nonzero(d == -1)[0]))


def _speed(rec):
    sp = rec.get("speed")
    if sp is not None and np.isfinite(sp).any():
        return sp
    d, t = rec["dist"], rec["t"]
    if not np.isfinite(d).any():
        return np.full(len(t), np.nan)
    dd = np.diff(d, prepend=d[0])
    dt = np.diff(t, prepend=t[0])
    with np.errstate(all="ignore"):
        return np.where(dt > 0, dd / dt, np.nan)


def check_record(rec, family="foot"):
    """Leave out heart-rate readings that fail a check, in place. Returns notes
    naming what was left out, for the session's card."""
    if not rec or len(rec["t"]) < 10:
        return []
    t, hr = rec["t"], rec["hr"]
    if not np.isfinite(hr).any():
        return []
    notes = []
    hr0 = hr.copy()

    # spike
    _, hg = _grid(t, hr)
    med = _rolling_median(hg, 11)
    dev = np.abs(hr - _at(med, t))
    spike = np.isfinite(dev) & (dev > SPIKE_BPM)
    if spike.any():
        hr[spike] = np.nan
        top = int(np.nanmax(hr0[spike]))
        notes.append(f"{int(spike.sum())} heart-rate readings jumped more than {SPIKE_BPM} bpm from the "
                     f"seconds around them and were left out (highest {top})")

    # stuck, while moving
    sp = _speed(rec)
    moving = np.isfinite(sp) & (sp > 0.5)
    same = np.concatenate(([False], np.diff(hr) == 0))
    stuck_s = 0.0
    for a, b in _runs(same):
        a = max(a - 1, 0)
        if b - a >= STUCK_N and t[b - 1] - t[a] >= STUCK_S and moving[a:b].mean() > 0.5:
            stuck_s += t[b - 1] - t[a]
            hr[a:b] = np.nan
    if stuck_s:
        notes.append(f"heart rate did not change for {round(stuck_s / 60)} min while moving "
                     f"(a sensor that stopped updating); those readings were left out")

    # cadence lock, on foot at running cadence
    cad = rec.get("cad")
    if family == "foot" and cad is not None and np.isfinite(cad).any():
        steps = cad * rec.get("cad_scale", 2)
        _, sg = _grid(t, steps)
        _, hg = _grid(t, hr)
        _, mg = _grid(t, moving.astype(float))
        run_cad = np.isfinite(sg) & (sg >= LOCK_SPM) & (mg > 0.5)
        locked = np.zeros(len(hg), dtype=bool)
        what = []
        for mult, name in ((1.0, "step"), (0.5, "stride")):
            gap = np.where(run_cad, np.abs(hg - sg * mult), np.nan)
            m = _rolling_median(gap, LOCK_S + 1)
            hit = np.isfinite(m) & (m < LOCK_BPM)
            if hit.any():
                # widen each hit to the whole window it stands for
                w = np.convolve(hit.astype(int), np.ones(LOCK_S + 1, dtype=int), mode="same") > 0
                w &= run_cad
                locked |= w
                what.append((name, float(np.nanmedian(sg[w] * mult)), float(np.nanmedian(hg[w]))))
        # A lock shows as heart rate jumping up to the cadence while the pace
        # stays the same. Where heart rate matches cadence but reached it with
        # the pace rising, it may be real: kept, and named as doubtful.
        _, vg = _grid(t, sp)
        out_min = kept_min = 0.0
        for a, b in _runs(locked):
            if b - a < 60:
                continue
            before = slice(max(0, a - LOCK_S), a)
            hb, hd = np.nanmedian(hg[before]) if np.isfinite(hg[before]).any() else np.nan, np.nanmedian(hg[a:b])
            vb, vd = np.nanmedian(vg[before]) if np.isfinite(vg[before]).any() else np.nan, np.nanmedian(vg[a:b])
            jumped = np.isfinite(hb) and hd - hb >= LOCK_JUMP_BPM
            pace_same = np.isfinite(vb) and vb > 0 and vd / vb < 1.15
            if jumped and pace_same:
                sel = (t - t[0] >= a) & (t - t[0] < b)
                hr[sel] = np.nan
                out_min += (b - a) / 60
            else:
                kept_min += (b - a) / 60
        desc = "; ".join(f"heart rate {round(h)} against a {n} rate of {round(s)}" for n, s, h in what)
        if out_min >= 1:
            notes.append(f"for {round(out_min, 1)} min on foot the heart rate jumped up to the cadence with the "
                         f"pace unchanged ({desc}): the sensor likely counted arm swing, so those readings "
                         f"were left out")
        if kept_min >= 1:
            notes.append(f"for {round(kept_min, 1)} min on foot the heart rate matched the cadence ({desc}) but "
                         f"rose to it with the effort, so those readings were kept; they may still be arm swing")
    rec["hr"] = hr
    return notes


def credible_peak(rec, window_s=60, tries=3):
    """The highest heart rate held for window_s seconds that was climbed to.
    Returns (peak or None, notes). A window with no ramp into it is left out
    and the next highest is tried."""
    notes = []
    if not rec or len(rec["t"]) < 10 or not np.isfinite(rec["hr"]).any():
        return None, notes
    g, hg = _grid(rec["t"], rec["hr"])
    if len(hg) < window_s + 1:
        return None, notes
    c = np.concatenate(([0.0], np.cumsum(np.nan_to_num(hg))))
    k = np.concatenate(([0], np.cumsum(np.isnan(hg))))
    means = (c[window_s:] - c[:-window_s]) / window_s
    means[(k[window_s:] - k[:-window_s]) > window_s // 4] = np.nan
    for _ in range(tries):
        if not np.isfinite(means).any():
            return None, notes
        i = int(np.nanargmax(means))
        peak = float(means[i])
        lead = hg[max(0, i - RAMP_S):i]
        lead = lead[np.isfinite(lead)]
        if len(lead) >= 30 and np.nanmax(lead) < peak - RAMP_BPM:
            notes.append(f"a one-minute heart rate of {round(peak)} with no rise into it (the two minutes "
                         f"before never passed {round(float(np.nanmax(lead)))}) was left out of peaks")
            means[max(0, i - window_s):i + window_s] = np.nan
            continue
        return peak, notes
    return None, notes


def hr_coverage(bl):
    """Minutes moving with and without heart rate."""
    mov = [b for b in bl if not b["pause"] and b["mode"] != "still"]
    tot = sum(b["dur"] for b in mov)
    miss = sum(b["dur"] for b in mov if not (isinstance(b["hr"], float) and math.isfinite(b["hr"])))
    return tot / 60, miss / 60
