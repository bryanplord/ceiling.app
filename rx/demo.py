"""Synthetic athlete for tests and for developing the web app without real data.

True physiology baked in: max HR 182, threshold 165, aerobic threshold 145,
resting HR ~50, HRV ~62 ms that drops after big days and recovers over ~2 days,
and aerobic efficiency that slowly improves (so "progress" has something to find).
"""
from __future__ import annotations

import math
import random
from datetime import date, timedelta

from .store import Activity, Store, Wellness
from .clock import local_today

TRUE_MAX = 182
TRUE_LTHR = 165
TRUE_LT1 = 145


def hr_session(minutes: int, base: float, efforts: list[tuple[int, int, float]] = (),
               rng: random.Random | None = None, drift_per_min: float = 0.0) -> tuple[list[int], list[float]]:
    """efforts: (start_min, dur_min, target_bpm). HR ramps toward targets with lag."""
    rng = rng or random.Random(0)
    n = minutes * 60
    hr, cur = [], 80.0
    for t in range(n):
        target = base + drift_per_min * t / 60
        for s, d, b in efforts:
            if s * 60 <= t < (s + d) * 60:
                target = b
        cur += (target - cur) / 25.0
        hr.append(round(cur + rng.gauss(0, 1.2), 1))
    return list(range(n)), hr


def steady_run(minutes: int, hr_target: float, fitness: float, rng: random.Random):
    """Constant speed. Above the aerobic threshold, HR drifts up at that speed."""
    drift = 0.45 if hr_target > TRUE_LT1 else 0.02
    t, h = hr_session(minutes, hr_target, [], rng, drift_per_min=drift)
    speed = (hr_target - 60) * 0.034 * fitness
    v = [round(speed + rng.gauss(0, 0.05), 3) for _ in t]
    return t, h, v


def seed(store: Store, days: int = 120, end: date | None = None, rng_seed: int = 7) -> None:
    rng = random.Random(rng_seed)
    end = end or local_today()
    start = end - timedelta(days=days)
    d, i, prev_load_big, hrv_debt = start, 0, False, 0.0
    run_targets = [136, 141, 150, 138, 153, 143]
    while d <= end:
        ds = d.isoformat()
        dow = d.weekday()
        day_n = (d - start).days
        hrv_debt *= 0.45                                  # recovers over ~2 days
        hrv = 62 * math.exp(rng.gauss(0, 0.07) - hrv_debt)
        rhr = 50 + rng.gauss(0, 1.2) - day_n * 0.01 + hrv_debt * 8
        sleep = min(98, max(40, rng.gauss(80, 7)))
        store.upsert_wellness("intervals", Wellness(ds, hrv=round(hrv, 1), rhr=round(rhr),
                                                    sleep_score=round(sleep), sleep_secs=7.3 * 3600))
        if d < end:  # today: sleep data only, no session yet
            fitness = 1 + day_n * 0.0006
            aid = f"demo:{ds}"
            speed = None
            if dow == 1:   # all-out 20-minute climb (~104% of threshold)
                sport, raw, mins = "bike", "MountainBikeRide", 75
                t, h = hr_session(mins, 130, [(20, 20, round(TRUE_LTHR * 1.04))], rng)
            elif dow in (3, 0):   # steady runs at varied HR: these reveal the aerobic threshold
                sport, raw, mins = "run", "Run", 50
                t, h, speed = steady_run(mins, run_targets[i % len(run_targets)], fitness, rng)
            elif dow == 5:   # long ride, sometimes with an all-out finish
                sport, raw, mins = "bike", "MountainBikeRide", 140
                eff = [(60, 10, 170), (100, 3, TRUE_MAX - 1)]
                if i % 3 == 0:
                    eff.append((mins - 15, 4, TRUE_MAX + 1))
                t, h = hr_session(mins, 132, eff, rng)
                hrv_debt += 0.35
            elif dow == 6:
                sport, raw, mins = "walk", "Walk", 60
                t, h, speed = steady_run(mins, 100, fitness * 0.6, rng)
            else:
                d += timedelta(days=1)
                continue
            store.upsert_activity(Activity(aid, "demo", ds, sport, mins * 60, start_local=f"{ds}T09:00:00",
                                           raw_type=raw, name=f"Demo {sport}",
                                           avg_hr=sum(h) / len(h), max_hr=max(h)))
            store.upsert_stream(aid, t, h, None, speed)
            i += 1
        d += timedelta(days=1)
