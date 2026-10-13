"""Synthetic athlete for tests and for developing the web app without real data.

True physiology baked in: max HR 182, threshold ~165, resting HR ~50, HRV ~62 ms.
"""
from __future__ import annotations

import math
import random
from datetime import date, timedelta

from .store import Activity, Store, Wellness

TRUE_MAX = 182
TRUE_LTHR = 165


def hr_session(minutes: int, base: float, efforts: list[tuple[int, int, float]] = (),
               rng: random.Random | None = None) -> tuple[list[int], list[float]]:
    """efforts: (start_min, dur_min, target_bpm). HR ramps toward targets with lag."""
    rng = rng or random.Random(0)
    n = minutes * 60
    hr, cur = [], 80.0
    for t in range(n):
        target = base
        for s, d, b in efforts:
            if s * 60 <= t < (s + d) * 60:
                target = b
        cur += (target - cur) / 25.0
        hr.append(round(cur + rng.gauss(0, 1.2), 1))
    return list(range(n)), hr


def seed(store: Store, days: int = 120, end: date | None = None, rng_seed: int = 7) -> None:
    rng = random.Random(rng_seed)
    end = end or date.today()
    start = end - timedelta(days=days)
    d = start
    i = 0
    while d <= end:
        ds = d.isoformat()
        dow = d.weekday()
        # wellness: HRV dips after big days
        hrv = 62 * math.exp(rng.gauss(0, 0.08))
        rhr = 50 + rng.gauss(0, 1.2)
        sleep = min(98, max(40, rng.gauss(80, 7)))
        store.upsert_wellness("intervals", Wellness(ds, hrv=round(hrv, 1), rhr=round(rhr),
                                                    sleep_score=round(sleep), sleep_secs=7.3 * 3600))
        # Tuesday: an all-out 20-minute climb (~104% of threshold)
        plan = {1: ("bike", 75, 130, [(20, 20, round(TRUE_LTHR * 1.04))]),
                3: ("run", 50, 140, []),
                5: ("bike", 140, 132, [(60, 10, 170), (100, 3, TRUE_MAX - 1)]),
                6: ("walk", 60, 100, [])}.get(dow)
        if plan and d < end:      # today: sleep data only, no session yet
            sport, mins, base, eff = plan
            if i % 5 == 0 and sport == "bike":   # occasionally an all-out climb
                eff = list(eff) + [(mins - 15, 4, TRUE_MAX + 1)]
            t, h = hr_session(mins, base, eff, rng)
            aid = f"demo:{ds}"
            store.upsert_activity(Activity(aid, "demo", ds, sport, mins * 60,
                                           start_local=f"{ds}T09:00:00", name=f"Demo {sport}",
                                           avg_hr=sum(h) / len(h), max_hr=max(h)))
            store.upsert_stream(aid, t, h)
            i += 1
        d += timedelta(days=1)
