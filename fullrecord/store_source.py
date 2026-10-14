"""New days from Ceiling's own store, read only.

Ceiling pulls each day from intervals.icu into its store: per session, time,
heart rate, cadence and speed streams, and power where there is a meter. Those
become sessions here exactly as export files do, with two gaps: the store keeps
no altitude or position, so these sessions show no climbing, and its overnight
figures are Garmin's own nightly calculations rather than raw readings.
"""
from __future__ import annotations

import os
from datetime import datetime

import numpy as np

from .catalogue import session_rows
from .fit import RECORD_FIELDS

SPORT = {"bike": "cycling"}   # Ceiling's buckets to the names catalogue.FAMILY knows


def _rec(t, hr, cad, speed, power):
    n = len(t)
    t = np.array(t, dtype=float)
    rec = {k: np.full(n, np.nan) for k in RECORD_FIELDS}
    rec["t"] = t - t[0]

    def arr(xs):
        return np.array([np.nan if v is None else float(v) for v in xs], dtype=float) if xs else np.full(n, np.nan)

    rec["hr"] = arr(hr)
    rec["cad"] = arr(cad) if cad and len(cad) == n else np.full(n, np.nan)
    if speed and len(speed) == n:
        sp = np.nan_to_num(arr(speed), nan=0.0)
        rec["speed"] = sp
        dt = np.diff(rec["t"], prepend=rec["t"][0])
        dt = np.where(dt > 10, 0.0, dt)   # no distance across a gap in recording
        rec["dist"] = np.cumsum(sp * dt)
    if power:
        pt, pw = power
        pt = np.array(pt, dtype=float)
        pw = np.array([np.nan if v is None else float(v) for v in pw], dtype=float)
        if len(pt) == len(pw) and len(pt):
            rec["power"] = np.interp(t, pt, pw, left=np.nan, right=np.nan)
    rec["cad_scale"] = 1   # intervals.icu reports running cadence in steps per minute
    return rec


def store_sessions(store, after, tz, caps):
    """Session rows for every session in Ceiling's store dated after `after`."""
    acts = []
    for a in store.activities(since=after):
        if after and a.date <= after:
            continue
        st = store.stream(a.id)
        if not st or len(st[0]) < 5:
            continue
        t, hr, cad = st
        rec = _rec(t, hr, cad, store.speed(a.id), store.power(a.id))
        try:
            start = datetime.fromisoformat((a.start_local or a.date).replace("Z", ""))
        except ValueError:
            continue
        start = start.replace(tzinfo=tz) if start.tzinfo is None else start
        acts.append({"rec": rec, "first_ts": start, "rec_first_ts": start,
                     "sport": SPORT.get(a.sport, a.raw_type or a.sport), "sub_sport": "generic",
                     "file": a.id})
    rows, _ = session_rows(acts, tz, caps=caps or {}, source="ceiling")
    return rows


def garmin_nightly_lines(store, after):
    """Garmin's own nightly figures for days after the export, if the person
    has chosen to include them (FULLRECORD_GARMIN_NIGHTLY=1). Off by default:
    they are Garmin's calculations, not raw readings."""
    if os.getenv("FULLRECORD_GARMIN_NIGHTLY") != "1":
        return []
    rows = [w for w in store.wellness(since=after) if not after or w.date > after]
    if not rows:
        return []
    out = ["## Nights since the export, Garmin's own nightly figures (from intervals.icu)",
           "These are Garmin's calculations, not raw readings: nightly HRV (ms), resting heart rate, "
           "sleep duration and Garmin's sleep score."]
    for w in rows:
        bits = [w.date]
        if w.hrv:
            bits.append(f"HRV {round(w.hrv)}")
        if w.rhr:
            bits.append(f"resting HR {round(w.rhr)}")
        if w.sleep_secs:
            bits.append(f"sleep {w.sleep_secs / 3600:.1f} h")
        if w.sleep_score:
            bits.append(f"Garmin sleep score {round(w.sleep_score)}")
        out.append(" | ".join(bits))
    return out
