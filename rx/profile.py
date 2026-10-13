"""Builds the athlete profile from everything in the store, as of a given day."""
from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta

from .analysis import hr as hra
from .analysis import load as la
from .analysis.recovery import resting_hr_baseline
from .sports import BIKE, RUN, SKI, WALK
from .store import Activity, Store, Wellness


@dataclass
class Profile:
    as_of: str
    max_hr: hra.MaxHR
    threshold: hra.Threshold
    zones: hra.Zones | None
    rest_hr: float
    activities: list[Activity]
    wellness: list[Wellness]
    session_load: dict[str, float]           # activity id -> TRIMP
    daily_load: dict[str, float]             # date -> TRIMP
    load_days: list[la.LoadDay]              # through the day before as_of
    typical_minutes: dict[str, float] = field(default_factory=dict)
    sport_mix: list[str] = field(default_factory=list)  # most → least common, last 28 days

    @property
    def ready(self) -> bool:
        return self.zones is not None

    @property
    def max_bpm(self) -> float:
        return self.max_hr.bpm or (self.threshold.bpm * 1.1 if self.threshold.bpm else 185)

    @property
    def yesterday(self) -> la.LoadDay | None:
        return self.load_days[-1] if self.load_days else None


def build(store: Store, as_of: str | None = None) -> Profile:
    as_of = as_of or date.today().isoformat()
    acts = [a for a in store.activities() if a.date <= as_of]
    wellness = [w for w in store.wellness() if w.date <= as_of]

    peaks, efforts = [], []
    for a in acts:
        if a.date >= as_of:      # judge the morning: today's sessions don't count yet
            continue
        st = store.stream(a.id)
        if not st:
            continue
        t, h, c = st
        p = hra.credible_peak(a.id, a.date, a.sport, t, h, c)
        if p:
            peaks.append(p)
        if a.moving_s >= 25 * 60:
            efforts.append((a.date, hra.best_sustained(t, h, 20 * 60),
                            hra.best_sustained(t, h, 60 * 60) if a.moving_s >= 60 * 60 else None))

    mx = hra.estimate_max_hr(peaks)
    th = hra.estimate_threshold(efforts)
    zones = hra.zones_from_lthr(int(th.bpm), int(mx.bpm) if mx.bpm else None) if th.bpm else None
    rest = resting_hr_baseline([w for w in wellness if w.date < as_of])
    max_bpm = mx.bpm or (th.bpm * 1.1 if th.bpm else 185)

    session_load: dict[str, float] = {}
    daily: dict[str, float] = {}
    for a in acts:
        st = store.stream(a.id)
        if st:
            v = la.trimp(st[0], st[1], rest, max_bpm)
        else:
            v = la.trimp_from_summary(a.moving_s / 60, a.avg_hr, rest, max_bpm)
        session_load[a.id] = v
        daily[a.date] = daily.get(a.date, 0.0) + v

    yday = (date.fromisoformat(as_of) - timedelta(days=1)).isoformat()
    past = {d: v for d, v in daily.items() if d <= yday}
    start = min(past) if past else yday
    load_days = la.series(past, end=yday, start=start) if past else []

    cutoff90 = (date.fromisoformat(as_of) - timedelta(days=90)).isoformat()
    cutoff28 = (date.fromisoformat(as_of) - timedelta(days=28)).isoformat()
    typical = {}
    for sport in (BIKE, RUN, WALK, SKI):
        mins = [a.moving_s / 60 for a in acts if a.sport == sport and a.date >= cutoff90 and a.moving_s > 600]
        if mins:
            typical[sport] = statistics.median(mins)
    mix = [s for s, _ in Counter(a.sport for a in acts if a.date >= cutoff28 and a.date < as_of).most_common()]

    return Profile(as_of, mx, th, zones, rest, acts, wellness, session_load, daily,
                   load_days, typical, mix)
