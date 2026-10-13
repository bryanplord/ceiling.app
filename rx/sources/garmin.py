"""Garmin Connect via python-garminconnect (https://github.com/cyberjunky/python-garminconnect).

PERSONAL PROTOTYPE ONLY. This logs in as the user, not through Garmin's approved
developer program. It may conflict with Garmin's terms and breaks when Garmin
changes its login. Never ship it to customers.

The user types their own password and MFA code, once, on their own machine.
Tokens are cached in RX_DATA_DIR/garmin_tokens and treated like a password.
"""
from __future__ import annotations

import getpass
from datetime import date, timedelta
from pathlib import Path

from ..sports import normalize
from ..store import Activity, Store, Wellness
from ..clock import local_today


def _lib():
    try:
        import garminconnect
    except ImportError as e:  # pragma: no cover
        raise SystemExit("Install the extra: pip install -e '.[garmin]'") from e
    return garminconnect


def login_interactive(token_dir: Path):
    gc = _lib()
    email = input("Garmin email: ").strip()
    password = getpass.getpass("Garmin password (not stored): ")
    api = gc.Garmin(email, password, prompt_mfa=lambda: input("MFA code: ").strip())
    token_dir.mkdir(parents=True, exist_ok=True)
    api.login(str(token_dir))
    return api


def client(token_dir: Path):
    gc = _lib()
    if not token_dir.exists():
        raise SystemExit("No Garmin session yet. Run: rx garmin-login")
    api = gc.Garmin()
    api.login(str(token_dir))
    return api


def _dig(d, *path):
    for p in path:
        if not isinstance(d, dict):
            return None
        d = d.get(p)
    return d


def wellness_for(api, day: str) -> tuple[Wellness, dict]:
    raw: dict = {}
    for name, fn in (("hrv", api.get_hrv_data), ("sleep", api.get_sleep_data),
                     ("rhr", api.get_rhr_day), ("readiness", api.get_training_readiness)):
        try:
            raw[name] = fn(day)
        except Exception as e:  # each endpoint fails independently on some devices/days
            raw[name] = {"error": str(e)}

    hrv = _dig(raw["hrv"], "hrvSummary", "lastNightAvg")
    sleep_dto = _dig(raw["sleep"], "dailySleepDTO") or {}
    sleep_score = _dig(sleep_dto, "sleepScores", "overall", "value")
    sleep_secs = sleep_dto.get("sleepTimeSeconds")
    rhr = _dig(raw["sleep"], "restingHeartRate") or _first_rhr(raw["rhr"])
    rd = raw["readiness"]
    readiness = rd[0].get("score") if isinstance(rd, list) and rd and isinstance(rd[0], dict) else None
    return Wellness(date=day, hrv=hrv, rhr=rhr, sleep_score=sleep_score,
                    sleep_secs=sleep_secs, readiness=readiness), raw


def _first_rhr(rhr_raw):
    vals = _dig(rhr_raw, "allMetrics", "metricsMap", "WELLNESS_RESTING_HEART_RATE")
    if isinstance(vals, list) and vals:
        return vals[0].get("value")
    return None


def activity_from_garmin(a: dict) -> Activity:
    start = a.get("startTimeLocal") or ""
    type_key = _dig(a, "activityType", "typeKey")
    return Activity(
        id=f"garmin:{a['activityId']}",
        source="garmin",
        date=start[:10],
        start_local=start.replace(" ", "T") or None,
        sport=normalize(type_key),
        raw_type=type_key,
        name=a.get("activityName"),
        moving_s=int(a.get("movingDuration") or a.get("duration") or 0),
        distance_m=a.get("distance"),
        avg_hr=a.get("averageHR"),
        max_hr=a.get("maxHR"),
        device=str(a.get("deviceId") or "") or None,
    )


def sync(api, store: Store, days: int = 60, activities: bool = False, log=print) -> dict:
    """Wellness by default. Activities only if intervals.icu isn't covering them,
    otherwise the same session would be stored twice."""
    counts = {"wellness": 0, "activities": 0}
    today = local_today()
    for i in range(days, -1, -1):
        day = (today - timedelta(days=i)).isoformat()
        w, raw = wellness_for(api, day)
        store.upsert_wellness("garmin", w, raw)
        counts["wellness"] += 1
    if activities:
        for a in api.get_activities_by_date((today - timedelta(days=days)).isoformat(), today.isoformat()):
            store.upsert_activity(activity_from_garmin(a), a)
            counts["activities"] += 1
    log(f"  garmin: {counts}")
    return counts
