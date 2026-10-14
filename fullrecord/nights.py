"""One row per night, from the raw overnight measurements the watch recorded.

Sources, all from the Connect uploads (the watch itself keeps only a few days):

- monitoring files: heart rate through the day and night (about one reading a
  minute) and breathing rate;
- HRV files: the watch's overnight HRV readings, one value (ms) about every
  five minutes;
- sleep files: only the time the watch says sleep started and ended, used to
  set the window for the night.

Left out under the raw-data rule: sleep stages and scores, HRV status,
baselines and weekly averages, Garmin's resting heart rate, stress and body
battery. Where there is no sleep file for a night, the window is a fixed
00:00 to 06:00 local time and the row says so.

Deterministic; no AI.
"""
from __future__ import annotations

import csv
import glob
import json
import os
import statistics
import sys
from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from garmin_fit_sdk import Decoder, Stream

FIT_EPOCH = 631065600  # 1989-12-31 00:00 UTC, in Unix seconds
SLEEP_TIMES = "521"     # undocumented message holding sleep start (field 3) and end (field 5)
FIXED_START_H, FIXED_END_H = 0, 6
MIN_HR_READINGS = 60    # fewer than this in the window and the night is not reported


def _unix(v):
    if isinstance(v, datetime):
        return int(v.replace(tzinfo=v.tzinfo or timezone.utc).timestamp())
    if isinstance(v, (int, float)):
        return int(v) + FIT_EPOCH
    return None


def _decode(path):
    try:
        msgs, _ = Decoder(Stream.from_file(path)).read()
        return msgs
    except Exception:
        return None


def collect(files):
    hr, resp, hrv, sleeps = [], [], [], []
    kinds = defaultdict(int)
    for path in files:
        msgs = _decode(path)
        if not msgs:
            kinds["unreadable"] += 1
            continue
        ftype = str((msgs.get("file_id_mesgs") or [{}])[0].get("type"))
        kinds[ftype] += 1
        if ftype == "monitoring_b":
            last = None
            for m in msgs.get("monitoring_mesgs", []):
                if m.get("timestamp") is not None:
                    last = _unix(m["timestamp"])
                    ts = last
                elif m.get("timestamp_16") is not None and last is not None:
                    fit_last = last - FIT_EPOCH
                    ts = last + ((m["timestamp_16"] - (fit_last & 0xFFFF)) & 0xFFFF)
                    last = ts
                else:
                    continue
                v = m.get("heart_rate")
                if v and 25 <= v <= 230:
                    hr.append((ts, v))
            for m in msgs.get("respiration_rate_mesgs", []):
                v, ts = m.get("respiration_rate"), _unix(m.get("timestamp"))
                if ts and v and v > 0:
                    resp.append((ts, v))
        elif ftype == "68":  # HRV file
            for m in msgs.get("hrv_value_mesgs", []):
                v, ts = m.get("value"), _unix(m.get("timestamp"))
                if ts and v and 0 < v < 300:
                    hrv.append((ts, v))
        elif ftype == "49":  # sleep file: start and end only
            for m in msgs.get(SLEEP_TIMES, []):
                s, e = m.get(3), m.get(5)
                if isinstance(s, int) and isinstance(e, int) and 0 < e - s < 16 * 3600:
                    sleeps.append((s + FIT_EPOCH, e + FIT_EPOCH))
                    break
            else:
                lv = [_unix(m.get("timestamp")) for m in msgs.get("sleep_level_mesgs", [])]
                lv = [t for t in lv if t]
                if len(lv) >= 2:
                    sleeps.append((min(lv), max(lv) + 60))
    for xs in (hr, resp, hrv):
        xs.sort()
    return {"hr": dedupe(hr), "resp": dedupe(resp), "hrv": dedupe(hrv),
            "sleeps": sorted(set(sleeps)), "kinds": dict(kinds)}


def dedupe(pairs):
    out, seen = [], set()
    for ts, v in pairs:
        if ts not in seen:
            seen.add(ts)
            out.append((ts, v))
    return out


def window(pairs, a, b):
    ts = [p[0] for p in pairs]
    return pairs[bisect_left(ts, a):bisect_right(ts, b)]


def lowest_held(pairs, span=1800, need=10):
    """Lowest mean heart rate over any 30 minutes with at least `need` readings."""
    best = None
    j = 0
    for i in range(len(pairs)):
        while j < len(pairs) and pairs[j][0] < pairs[i][0] + span:
            j += 1
        chunk = [v for _, v in pairs[i:j]]
        if len(chunk) >= need:
            m = sum(chunk) / len(chunk)
            best = m if best is None or m < best else best
    return best


def r(v, n=0):
    return None if v is None else round(v, n) if n else int(round(v))


def nights(data, tz):
    rows = {}
    for s, e in data["sleeps"]:
        night = datetime.fromtimestamp(e, tz).date()
        rows[night] = {"window": "sleep", "a": s, "b": e}
    days = sorted({datetime.fromtimestamp(t, tz).date() for t, _ in data["hr"]})
    for d in days:
        if d in rows:
            continue
        a = int(datetime(d.year, d.month, d.day, FIXED_START_H, tzinfo=tz).timestamp())
        b = int(datetime(d.year, d.month, d.day, FIXED_END_H, tzinfo=tz).timestamp())
        rows[d] = {"window": "fixed", "a": a, "b": b}
    out = []
    for d in sorted(rows):
        w = rows[d]
        h = window(data["hr"], w["a"], w["b"])
        if len(h) < MIN_HR_READINGS and w["window"] == "fixed":
            continue
        v = window(data["hrv"], w["a"], w["b"])
        rp = window(data["resp"], w["a"], w["b"])
        hv = [x for _, x in v]
        hrs = [x for _, x in h]
        row = {
            "night": d.isoformat(),
            "weekday": d.strftime("%a"),
            "window": w["window"],
            "start": datetime.fromtimestamp(w["a"], tz).strftime("%H:%M") if w["window"] == "sleep" else "",
            "end": datetime.fromtimestamp(w["b"], tz).strftime("%H:%M") if w["window"] == "sleep" else "",
            "hours": round((w["b"] - w["a"]) / 3600, 2) if w["window"] == "sleep" else "",
            "hr_readings": len(hrs),
            "hr_mean": r(statistics.fmean(hrs)) if hrs else None,
            "hr_low_30min": r(lowest_held(h)) if hrs else None,
            "hr_min": min(hrs) if hrs else None,
            "hrv_readings": len(hv),
            "hrv_mean": r(statistics.fmean(hv)) if hv else None,
            "hrv_median": r(statistics.median(hv)) if hv else None,
            "hrv_high": r(max(hv)) if hv else None,
            "hrv_low": r(min(hv)) if hv else None,
            "breaths_mean": r(statistics.fmean([x for _, x in rp]), 1) if rp else None,
        }
        out.append(row)
    return out
