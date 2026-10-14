"""New days from intervals.icu, for an athlete whose own data reaches us that way
(Bryan: his Garmin export for the history, his intervals.icu account after it).

Each pull downloads the original file of every activity since the last pull
(intervals.icu keeps the file Garmin sent it, with every second recorded) and
reads it with the same code as the export, using the limits already computed
from the athlete's own history. Nights: intervals.icu holds only Garmin's
nightly figures (HRV, resting heart rate, sleep time and score), not the raw
overnight readings, so these are kept in FULLRECORD_HOME/wellness.json and
shown in the record as Garmin's figures, separate from the raw nights.

Needs INTERVALS_API_KEY (and optionally INTERVALS_ATHLETE_ID; "0" means the
key's own athlete).
"""
from __future__ import annotations

import gzip
import json
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from . import db

BASE = "https://intervals.icu/api/v1/"


def _auth():
    k = os.getenv("INTERVALS_API_KEY")
    if not k:
        raise SystemExit("Set INTERVALS_API_KEY.")
    return ("API_KEY", k), os.getenv("INTERVALS_ATHLETE_ID") or "0"


def _get(url, auth, **kw):
    for _ in range(5):
        r = requests.get(url, auth=auth, timeout=120, **kw)
        if r.status_code == 429:
            time.sleep(int(r.headers.get("Retry-After", "30")))
            continue
        r.raise_for_status()
        return r
    r.raise_for_status()


def pull(store, home: Path, tz_name: str, log=print) -> dict:
    from .garmin_live import ingest
    tz = ZoneInfo(tz_name)
    auth, athlete = _auth()
    today = datetime.now(tz).date()
    last = db.get_meta(store, "intervals_last_pull")
    export_end = db.get_meta(store, "export_end")
    if last:
        start = date.fromisoformat(last[:10]) - timedelta(days=1)
    elif export_end:
        start = date.fromisoformat(export_end) - timedelta(days=1)
    else:
        start = today - timedelta(days=30)

    folder = home / "intervals" / "activities"
    folder.mkdir(parents=True, exist_ok=True)
    acts = _get(f"{BASE}athlete/{athlete}/activities", auth,
                params={"oldest": start.isoformat(), "newest": today.isoformat()}).json()
    new = []
    for a in acts:
        p = folder / f"{a['id']}.fit"
        if p.exists():
            continue
        data = _get(f"{BASE}activity/{a['id']}/file", auth).content
        if data[:2] == b"\x1f\x8b":
            data = gzip.decompress(data)
        if len(data) > 12 and data[8:12] == b".FIT":
            p.write_bytes(data)
            new.append(p)
        time.sleep(0.2)
    counts = ingest(store, "", {"activities": new, "days": []}, start, tz)

    since = export_end or (today - timedelta(days=30)).isoformat()
    rows = _get(f"{BASE}athlete/{athlete}/wellness", auth,
                params={"oldest": since, "newest": today.isoformat()}).json()
    keep = [{"date": w["id"], "hrv": w.get("hrv"), "rhr": w.get("restingHR"),
             "sleep_secs": w.get("sleepSecs"), "sleep_score": w.get("sleepScore")}
            for w in rows if w.get("hrv") or w.get("restingHR") or w.get("sleepSecs")]
    (home / "wellness.json").write_text(json.dumps(keep))

    db.set_meta(store, "intervals_last_pull", datetime.now(tz).isoformat(timespec="minutes"))
    if counts["sessions"]:
        db.set_meta(store, "ingested", datetime.now(tz).isoformat(timespec="minutes"))
    store.sync()
    log(f"  intervals.icu: {len(new)} activity files, {counts['sessions']} sessions saved, "
        f"{len(keep)} days of Garmin's nightly figures")
    return counts
