"""Send the athlete's Garmin data on to intervals.icu, so that apps reading
intervals.icu (Ceiling) get the full record: every original activity file, and
each night's overnight figures where intervals.icu has none of its own.

    python -m fullrecord.intervals_feed history FOLDER   once: every activity file under FOLDER,
                                                         and every night in the record
    python -m fullrecord.intervals_feed recent           each pull: new activity files from
                                                         Garmin Connect, and the last few nights

intervals.icu recognises a file it already has and returns the same activity,
so sending one twice does no harm. Files already sent are listed in
FULLRECORD_HOME/intervals_sent.json so they are not sent again.

Nights are written only where intervals.icu has no HRV for that date, so the
figures Garmin sends intervals.icu itself are never overwritten. What is sent:
hrv (the mean of the watch's overnight HRV readings, which is how Garmin
reports its nightly HRV), restingHR (the lowest 30-minute mean heart rate
during the night), avgSleepingHR, sleepSecs (from the sleep window) and
respiration.

Needs INTERVALS_ATHLETE_ID and INTERVALS_API_KEY in the environment.
"""
from __future__ import annotations

import glob
import json
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests

from . import db

BASE = "https://intervals.icu/api/v1/athlete/"
RECENT_NIGHTS = 3


def _auth():
    a, k = os.getenv("INTERVALS_ATHLETE_ID"), os.getenv("INTERVALS_API_KEY")
    if not a or not k:
        raise SystemExit("Set INTERVALS_ATHLETE_ID and INTERVALS_API_KEY.")
    return BASE + a, ("API_KEY", k)


def _state_path() -> Path:
    from .standalone import home
    return home() / "intervals_sent.json"


def _load_state() -> dict:
    p = _state_path()
    return json.loads(p.read_text()) if p.exists() else {}


def _save_state(state: dict) -> None:
    p = _state_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    tmp.replace(p)


def _post_file(url, auth, path) -> tuple[int, str]:
    for attempt in range(5):
        with open(path, "rb") as f:
            r = requests.post(url + "/activities", auth=auth, files={"file": (Path(path).name, f)}, timeout=120)
        if r.status_code == 429:
            time.sleep(int(r.headers.get("Retry-After", "30")))
            continue
        return r.status_code, r.text[:200]
    return 429, "rate limited"


def send_activities(paths, log=print) -> dict:
    """Send each activity file not sent before. Returns counts."""
    from .catalogue import _fit_file_type
    url, auth = _auth()
    state = _load_state()
    sent = state.setdefault("files", {})
    counts = {"sent": 0, "skipped": 0, "not_activity": 0, "failed": 0}
    for i, p in enumerate(paths, 1):
        key = os.path.abspath(p)
        if key in sent:
            counts["skipped"] += 1
            continue
        if _fit_file_type(p) != "activity":
            counts["not_activity"] += 1
            continue
        code, body = _post_file(url, auth, p)
        if code in (200, 201):
            try:
                sent[key] = json.loads(body).get("id") or "ok"
            except ValueError:
                sent[key] = "ok"
            counts["sent"] += 1
        else:
            counts["failed"] += 1
            log(f"  {Path(p).name}: {code} {body}")
        if i % 50 == 0:
            _save_state(state)
            log(f"  {i} of {len(paths)} files looked at: {counts}")
        time.sleep(0.3)
    _save_state(state)
    return counts


def send_nights(store, since: str | None = None, log=print) -> int:
    """Write each night from the record where intervals.icu has no HRV for that date."""
    url, auth = _auth()
    nights = [n for n in db.nights(store) if not since or n["night"] >= since]
    if not nights:
        return 0
    oldest, newest = nights[0]["night"], nights[-1]["night"]
    r = requests.get(url + "/wellness", auth=auth, params={"oldest": oldest, "newest": newest}, timeout=60)
    r.raise_for_status()
    have = {w["id"] for w in r.json() if w.get("hrv")}
    n_sent = 0
    for n in nights:
        if n["night"] in have:
            continue
        body = {}
        if n.get("hrv_mean") is not None:
            body["hrv"] = n["hrv_mean"]
        if n.get("hr_low_30min") is not None:
            body["restingHR"] = n["hr_low_30min"]
        if n.get("hr_mean") is not None:
            body["avgSleepingHR"] = n["hr_mean"]
        if n.get("window") == "sleep" and n.get("hours"):
            body["sleepSecs"] = int(float(n["hours"]) * 3600)
        if n.get("breaths_mean") is not None:
            body["respiration"] = n["breaths_mean"]
        if not body:
            continue
        w = requests.put(f"{url}/wellness/{n['night']}", auth=auth, json=body, timeout=60)
        if w.status_code == 429:
            time.sleep(int(w.headers.get("Retry-After", "30")))
            w = requests.put(f"{url}/wellness/{n['night']}", auth=auth, json=body, timeout=60)
        if w.ok:
            n_sent += 1
        else:
            log(f"  night {n['night']}: {w.status_code} {w.text[:200]}")
        time.sleep(0.2)
    return n_sent


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in ("history", "recent"):
        print(__doc__)
        return 2
    from . import garmin_live
    from .standalone import open_store
    store = open_store()
    if argv[0] == "history":
        folder = argv[1]
        paths = sorted({f for pat in ("*.fit", "*.FIT") for f in glob.glob(os.path.join(folder, "**", pat), recursive=True)})
        live = sorted(glob.glob(str(garmin_live.base_dir("") / "activities" / "*.fit")))
        print(f"{len(paths) + len(live)} files to look at", flush=True)
        print("activities:", send_activities(paths + live), flush=True)
        print("nights written:", send_nights(store), flush=True)
    else:
        live = sorted(glob.glob(str(garmin_live.base_dir("") / "activities" / "*.fit")))
        counts = send_activities(live)
        since = (date.today() - timedelta(days=RECENT_NIGHTS)).isoformat()
        print(f"  intervals.icu: {counts['sent']} activity files sent, "
              f"{send_nights(store, since)} nights written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
