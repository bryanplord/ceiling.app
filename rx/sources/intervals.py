"""intervals.icu API client.

Docs: https://forum.intervals.icu/t/api-access-to-intervals-icu/609
Spec: https://intervals.icu/api-docs.html

- Basic auth: username is the literal "API_KEY", password is the key.
- Athlete id "0" means the key's owner.
- Limits (API key): 5000/day, 2500 per rolling 15 min, 10/s per IP. 429 carries Retry-After.
- Cloudflare may challenge non-browser user agents, so we send a browser-like one.
- Warning (9 Oct 2026): Garmin-sourced data may soon be blocked from this API.
"""
from __future__ import annotations

import time
from datetime import date, timedelta

import requests

from ..sports import normalize
from ..store import Activity, Store, Wellness
from ..clock import local_today

BASE = "https://intervals.icu/api/v1"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) rx/0.1"


class IntervalsClient:
    def __init__(self, api_key: str, athlete_id: str = "0", session: requests.Session | None = None):
        if not api_key:
            raise ValueError("INTERVALS_API_KEY is empty. Add it to .env.")
        self.athlete = athlete_id
        self.s = session or requests.Session()
        self.s.auth = ("API_KEY", api_key)
        self.s.headers["User-Agent"] = UA
        self._last = 0.0

    def _get(self, path: str, **params):
        for attempt in range(5):
            wait = 0.12 - (time.monotonic() - self._last)  # stay under 10/s
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            r = self.s.get(f"{BASE}{path}", params=params, timeout=60)
            if r.status_code == 429:
                time.sleep(min(int(r.headers.get("Retry-After", "30")), 900))
                continue
            if r.status_code in (401, 403):
                raise PermissionError(f"intervals.icu refused the key ({r.status_code}). "
                                      "Regenerate it under Settings → Developer Settings.")
            r.raise_for_status()
            return r.json()
        raise RuntimeError(f"intervals.icu kept rate-limiting {path}")

    def activities(self, oldest: str, newest: str) -> list[dict]:
        return self._get(f"/athlete/{self.athlete}/activities", oldest=oldest, newest=newest)

    def streams(self, activity_id: str,
                types=("time", "heartrate", "cadence", "velocity_smooth")) -> dict[str, list]:
        data = self._get(f"/activity/{activity_id}/streams.json", types=",".join(types))
        return {s["type"]: s.get("data") or [] for s in data if isinstance(s, dict)}

    def wellness(self, oldest: str, newest: str) -> list[dict]:
        return self._get(f"/athlete/{self.athlete}/wellness.json", oldest=oldest, newest=newest)


def activity_from_icu(a: dict) -> Activity:
    start = a.get("start_date_local") or ""
    return Activity(
        id=f"icu:{a['id']}",
        source="intervals",
        date=start[:10],
        start_local=start or None,
        sport=normalize(a.get("type")),
        raw_type=a.get("type"),
        name=a.get("name"),
        moving_s=int(a.get("moving_time") or a.get("elapsed_time") or 0),
        distance_m=a.get("distance"),
        avg_hr=a.get("average_heartrate"),
        max_hr=a.get("max_heartrate"),
        device=a.get("device_name"),
    )


def wellness_from_icu(w: dict) -> Wellness:
    return Wellness(
        date=str(w.get("id"))[:10],
        hrv=w.get("hrv"),
        rhr=w.get("restingHR"),
        sleep_score=w.get("sleepScore"),
        sleep_secs=w.get("sleepSecs"),
        readiness=w.get("readiness"),
    )


def sync(client: IntervalsClient, store: Store, days: int = 365,
         with_streams: bool = True, log=print) -> dict:
    newest = local_today()
    oldest = newest - timedelta(days=days)
    counts = {"activities": 0, "streams": 0, "wellness": 0}

    # Pull in 90-day windows so huge histories don't time out.
    cur = oldest
    while cur <= newest:
        end = min(cur + timedelta(days=90), newest)
        for raw in client.activities(cur.isoformat(), (end + timedelta(days=1)).isoformat()):
            if not raw.get("id") or not raw.get("start_date_local"):
                continue  # e.g. Strava stubs the API won't expose
            act = activity_from_icu(raw)
            store.upsert_activity(act, raw)
            counts["activities"] += 1
            if with_streams and raw.get("has_heartrate", True) and not store.has_stream(act.id):
                st = client.streams(str(raw["id"]))
                if st.get("heartrate"):
                    t = st.get("time") or list(range(len(st["heartrate"])))
                    store.upsert_stream(act.id, t, st["heartrate"], st.get("cadence"),
                                        st.get("velocity_smooth"))
                    counts["streams"] += 1
        # Ask one day past the window: last night is filed under this morning's date, and a
        # date-boundary or timezone mismatch must never make today's row arrive a day late.
        for raw in client.wellness(cur.isoformat(), (end + timedelta(days=1)).isoformat()):
            store.upsert_wellness("intervals", wellness_from_icu(raw), raw)
            counts["wellness"] += 1
        log(f"  {cur} → {end}: {counts}")
        cur = end + timedelta(days=1)
    return counts


def has_power(raw: dict) -> bool:
    return bool(raw.get("device_watts") or raw.get("icu_average_watts") or raw.get("average_watts")
                or raw.get("icu_weighted_avg_watts"))


def backfill_power(client: IntervalsClient, store: Store, limit: int = 80, log=print) -> int:
    """Fetch power streams for rides that have power, newest first, a batch per run."""
    n = 0
    for a in sorted(store.activities(), key=lambda a: a.date, reverse=True):
        if n >= limit:
            break
        if a.sport != "bike" or not a.id.startswith("icu:") or store.has_power(a.id):
            continue
        raw = store.raw(a.id)
        if not has_power(raw):
            continue
        st = client.streams(a.id[4:], types=("time", "watts"))
        w = st.get("watts") or []
        t = st.get("time") or list(range(len(w)))
        store.upsert_power(a.id, t if w else [], w)
        n += 1
    if n:
        log(f"  power streams: {n}")
    return n
