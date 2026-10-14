"""Read one Garmin FIT activity file into plain Python and numpy structures.

Nothing here interprets the data. The catalogue uses only `rec` (the
second-by-second records) and `rec_first_ts`; the session, zones and profile
blocks are Garmin's own labels and settings and are kept here only for
inspection, never read by the catalogue.
"""
from __future__ import annotations

import math
from datetime import timezone

import fitparse
import numpy as np

SEMI_TO_DEG = 180.0 / 2**31

# Sports the catalogue treats as aerobic training. Everything else becomes a
# marker on the timeline (date, duration, heart rate) with no card.
AEROBIC = {"running", "hiking", "cycling", "walking", "swimming",
           "e_biking", "mountaineering", "cross_country_skiing",
           "rowing", "paddling", "stand_up_paddleboarding", "snowshoeing"}

RECORD_FIELDS = {
    "hr": ("heart_rate",),
    "speed": ("enhanced_speed", "speed"),
    "alt": ("enhanced_altitude", "altitude"),
    "dist": ("distance",),
    "cad": ("cadence",),
    "power": ("power",),
    "temp": ("temperature",),
    "lat": ("position_lat",),
    "lon": ("position_long",),
    "vo": ("vertical_oscillation",),
    "gct": ("stance_time",),
    "step": ("step_length",),
}


def _first(values: dict, names):
    for n in names:
        v = values.get(n)
        if v is not None:
            return v
    return None


def _clean(values: dict) -> dict:
    out = {}
    for k, v in values.items():
        if v is None or str(k).startswith("unknown"):
            continue
        if isinstance(v, tuple) and all(x is None for x in v):
            continue
        out[k] = v
    return out


def read_activity(path: str) -> dict:
    ff = fitparse.FitFile(path)
    session, zones, profile = {}, {}, {}
    sets = 0
    laps = []
    rows = []
    for m in ff.get_messages():
        name = m.name
        if name == "record":
            v = m.get_values()
            ts = v.get("timestamp")
            if ts is None:
                continue
            row = {"ts": ts}
            for key, names in RECORD_FIELDS.items():
                row[key] = _first(v, names)
            rows.append(row)
        elif name == "session" and not session:
            session = _clean(m.get_values())
        elif name == "zones_target":
            zones = _clean(m.get_values())
        elif name == "user_profile":
            profile = _clean(m.get_values())
        elif name == "lap":
            laps.append(_clean(m.get_values()))
        elif name == "set":
            sets += 1

    start = session.get("start_time") or (rows[0]["ts"] if rows else None)
    if start is not None and start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)

    rec = {}
    rec_first_ts = None
    if rows:
        rec_first_ts = rows[0]["ts"]
        if rec_first_ts.tzinfo is None:
            rec_first_ts = rec_first_ts.replace(tzinfo=timezone.utc)
        t0 = rows[0]["ts"]
        rec["t"] = np.array([(r["ts"] - t0).total_seconds() for r in rows], dtype=float)
        for key in RECORD_FIELDS:
            rec[key] = np.array([np.nan if r[key] is None else float(r[key]) for r in rows], dtype=float)
        rec["lat"] = rec["lat"] * SEMI_TO_DEG
        rec["lon"] = rec["lon"] * SEMI_TO_DEG

    sport = str(session.get("sport", "unknown"))
    sub_sport = str(session.get("sub_sport", "generic"))
    return {
        "file": path.split("/")[-1],
        "sport": sport,
        "sub_sport": sub_sport,
        "start": start,
        "rec_first_ts": rec_first_ts,
        "session": session,
        "zones": zones,
        "profile": profile,
        "laps": laps,
        "sets": sets,
        "rec": rec,
    }


def deg(semicircles):
    if semicircles is None:
        return None
    return semicircles * SEMI_TO_DEG


def finite(x) -> bool:
    return x is not None and not (isinstance(x, float) and math.isnan(x))
