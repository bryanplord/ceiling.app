"""Power targets for road and trainer rides: the watts that put YOUR heart in today's range.

Heart rate is the rule (it's what your body is doing); power is the steering wheel (it reacts
instantly, heart rate lags a minute). So the target is personal, not a percentage of FTP:

  1. From your own road and trainer rides with power in the last 90 days, take every 30 seconds
     where your heart rate sat in today's range, and read what you were pushing. The middle half
     of those watts (25th to 75th percentile) is the target. Needs 20 minutes of matched time.
  2. Not enough of that: Coggan zones on your FTP (Endurance 56-75% for easy, Tempo 76-87% for
     steady, 95-100% for threshold efforts).

Hard days always use FTP for the efforts themselves: 8 minutes at threshold is a power target,
and heart rate takes most of the effort to catch up.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np

from .distribution import ftp_estimate
from .hr import clean, rolling_mean_full

CONTROLLED_BIKES = {"RoadRide", "Ride", "VirtualRide", "GravelRide", "road_biking", "indoor_cycling", "virtual_ride",
                    "gravel_cycling", "cycling"}
FTP_BANDS = {"easy": (0.56, 0.75), "light": (0.45, 0.56), "steady": (0.76, 0.87), "hard": (0.95, 1.00)}
_cache: dict = {}


def _pairs(store, a) -> tuple[np.ndarray, np.ndarray] | None:
    key = a.id
    if key in _cache:
        return _cache[key]
    out = None
    pw, st = store.power(a.id), store.stream(a.id)
    if pw and st and len(pw[1]) >= 600:
        h = clean(st[0], st[1])
        hm = dict(zip((int(t) for t in st[0]), h))
        t = [int(x) for x in pw[0]]
        w = np.asarray([0.0 if v is None else float(v) for v in pw[1]])
        hh = np.asarray([hm.get(x, np.nan) for x in t], float)
        if np.sum(~np.isnan(hh)) > 600:
            hh = np.where(np.isnan(hh), np.nanmean(hh), hh)
            w30, h30 = rolling_mean_full(w, 30), rolling_mean_full(hh, 30)
            if len(w30):
                keep = w30 > 30                                   # pedaling, not coasting
                out = (w30[keep], h30[keep])
    _cache[key] = out
    return out


def target(store, p, verdict: str, hr_lo: int | None, hr_hi: int | None) -> dict | None:
    """{'lo','hi','ftp','basis','sentence'} or None when there's no power history at all."""
    if verdict not in FTP_BANDS:
        return None
    ftp, _ = ftp_estimate(store, p)
    if verdict != "hard" and hr_hi:
        cut = (date.fromisoformat(p.as_of) - timedelta(days=90)).isoformat()
        lo_hr = hr_lo if hr_lo is not None else hr_hi - 10
        ws = []
        for a in p.activities:
            if a.sport != "bike" or a.date < cut or a.date >= p.as_of or (a.raw_type or "") not in CONTROLLED_BIKES:
                continue
            pr = _pairs(store, a)
            if pr is None:
                continue
            w, h = pr
            ws.append(w[(h >= lo_hr) & (h <= hr_hi)])
        allw = np.concatenate(ws) if ws else np.array([])
        if len(allw) >= 20 * 60:
            lo, hi = np.percentile(allw, 25), np.percentile(allw, 75)
            lo, hi = int(round(lo / 5) * 5), int(round(hi / 5) * 5)
            return {"lo": lo, "hi": hi, "ftp": ftp, "basis": "yours",
                    "sentence": f"On the road bike or trainer, that's about {lo}–{hi} W: what you push at this heart rate on your own rides."}
    if not ftp:
        return None
    a, b = FTP_BANDS[verdict]
    lo, hi = int(round(ftp * a / 5) * 5), int(round(ftp * b / 5) * 5)
    if verdict == "hard":
        return {"lo": lo, "hi": hi, "ftp": ftp, "basis": "ftp",
                "sentence": f"Ride the efforts by power: {lo}–{hi} W (95–100% of your {int(ftp)} W FTP). Heart rate lags; it should reach the range by minute 3 or 4."}
    name = {"easy": "Endurance", "light": "Recovery", "steady": "Tempo"}[verdict]
    return {"lo": lo, "hi": hi, "ftp": ftp, "basis": "ftp",
            "sentence": f"On the road bike or trainer, about {lo}–{hi} W ({name}, from your {int(ftp)} W FTP). If heart rate says otherwise, heart rate wins."}
