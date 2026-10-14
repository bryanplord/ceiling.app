"""Builds the athlete profile from everything in the store, as of a given morning."""
from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta

from .analysis import aerobic as aer
from .analysis import hr as hra
from .analysis import load as la
from .analysis import progress as prog
from .analysis import response as resp
from .analysis.recovery import resting_hr_baseline
from .sports import BIKE, RUN, SKI, WALK
from .store import Activity, Store, Wellness
from .clock import local_today


@dataclass
class Profile:
    as_of: str
    max_hr: hra.MaxHR
    threshold: hra.Threshold
    aerobic: aer.AerobicThreshold
    zones: hra.Zones | None
    rest_hr: float
    activities: list[Activity]
    wellness: list[Wellness]
    session_load: dict[str, float]           # activity id -> TRIMP
    daily_load: dict[str, float]             # date -> TRIMP
    load_days: list[la.LoadDay]              # through the day before as_of
    response: resp.Response
    progress: prog.Progress
    typical_minutes: dict[str, float] = field(default_factory=dict)
    hr_hist: dict = field(default_factory=dict)
    program: object = None                   # the goal programme, attached by the service   # activity id -> seconds at each bpm (30-s smoothed), last 150 days
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


# What each activity's stream says that doesn't depend on the day you look from: cleaned heart
# rate, peak, best 20/60 minutes, steady-state efficiency, 30-s histogram. Building a profile for
# another morning (yesterday's plan, two weeks of Workouts, a History month) used to re-read and
# re-analyse every stream each time; now each stream is analysed once per data version.
_FACTS: dict = {}       # (db path, owner) -> (data version, {activity id: _Facts})


@dataclass
class _Facts:
    stamp: tuple
    h: object = None                         # cleaned 1 Hz heart rate (numpy), None without a stream
    peak: object = None
    e20: float | None = None
    e60: float | None = None
    steady: object = None
    hist: object = None                      # 30-s smoothed bpm histogram, None if too short
    hrr: dict = field(default_factory=dict)  # threshold bpm -> heart-rate recovery


def _facts(store: Store, a: Activity, memo: dict) -> _Facts:
    import numpy as _np
    stamp = (a.date, a.sport, a.raw_type, a.moving_s, a.avg_hr, a.max_hr, a.distance_m, a.start_local)
    f = memo.get(a.id)
    if f is not None and f.stamp == stamp:
        return f
    f = _Facts(stamp)
    st = store.stream(a.id)
    if st:
        t, h, c = st
        f.h = hra.clean(t, h)
        f.peak = hra.credible_peak(a.id, a.date, a.sport, t, h, c)
        if a.moving_s >= 25 * 60:
            f.e20 = hra.best_sustained(t, h, 20 * 60)
            f.e60 = hra.best_sustained(t, h, 60 * 60) if a.moving_s >= 60 * 60 else None
        f.steady = aer.steady_session(a.id, a.date, a.sport, a.raw_type, t, h, store.speed(a.id))
        v_ = f.h[~_np.isnan(f.h)]
        if len(v_) >= 60:
            v_ = _np.convolve(v_, _np.ones(30) / 30, mode="valid")
            f.hist = _np.bincount(_np.clip(_np.round(v_).astype(int), 0, 230), minlength=231)
    memo[a.id] = f
    return f


def build(store: Store, as_of: str | None = None, version=None) -> Profile:
    """version: the store's data version (service.data_version). When given, each activity's
    stream is analysed once per version and reused for every other morning's profile."""
    as_of = as_of or local_today().isoformat()
    acts = [a for a in store.activities() if a.date <= as_of]
    wellness = [w for w in store.wellness() if w.date <= as_of]
    past_acts = [a for a in acts if a.date < as_of]   # the morning view: today's sessions don't count yet

    if version is None:
        memo: dict = {}
    else:
        slot = (getattr(store, "path", ""), getattr(store, "owner", ""))
        hit = _FACTS.get(slot)
        if hit is None or hit[0] != version:
            hit = (version, {})
            _FACTS[slot] = hit
        memo = hit[1]
    facts = {a.id: _facts(store, a, memo) for a in acts}

    peaks, efforts, steady = [], [], []
    for a in past_acts:
        f = facts[a.id]
        if f.h is None:
            continue
        if f.peak:
            peaks.append(f.peak)
        if a.moving_s >= 25 * 60:
            efforts.append((a.date, f.e20, f.e60))
        if f.steady:
            steady.append(f.steady)

    mx = hra.estimate_max_hr(peaks)
    th = hra.estimate_threshold(efforts)
    lt1 = aer.estimate_lt1(steady, th.bpm)
    zones = None
    if th.bpm and lt1.bpm and lt1.bpm < th.bpm - 4:
        zones = hra.zones_from_anchors(lt1.bpm, int(th.bpm), int(mx.bpm) if mx.bpm else None)
    elif th.bpm:
        zones = hra.zones_from_lthr(int(th.bpm), int(mx.bpm) if mx.bpm else None)

    rest = resting_hr_baseline([w for w in wellness if w.date < as_of])
    max_bpm = mx.bpm or (th.bpm * 1.1 if th.bpm else 185)

    session_load: dict[str, float] = {}
    daily: dict[str, float] = {}
    for a in acts:
        if facts[a.id].h is not None:
            v = la.trimp_clean(facts[a.id].h, rest, max_bpm)
        else:
            v = la.trimp_from_summary(a.moving_s / 60, a.avg_hr, rest, max_bpm)
        session_load[a.id] = v
        daily[a.date] = daily.get(a.date, 0.0) + v

    yday = (date.fromisoformat(as_of) - timedelta(days=1)).isoformat()
    past = {d: v for d, v in daily.items() if d <= yday}
    load_days = la.series(past, end=yday, start=min(past)) if past else []

    hrr = []
    if th.bpm:
        for a in past_acts:
            f = facts[a.id]
            if f.h is not None:
                if th.bpm not in f.hrr:
                    f.hrr[th.bpm] = prog.hr_recovery_clean(f.h, th.bpm)
                v = f.hrr[th.bpm]
                if v:
                    hrr.append((a.date, v))

    cutoff90 = (date.fromisoformat(as_of) - timedelta(days=90)).isoformat()
    cutoff28 = (date.fromisoformat(as_of) - timedelta(days=28)).isoformat()
    typical = {}
    for sport in (BIKE, RUN, WALK, SKI):
        mins = [a.moving_s / 60 for a in past_acts if a.sport == sport and a.date >= cutoff90 and a.moving_s > 600]
        if mins:
            typical[sport] = statistics.median(mins)
    mix = [s for s, _ in Counter(a.sport for a in past_acts if a.date >= cutoff28).most_common()]

    cut150 = (date.fromisoformat(as_of) - timedelta(days=150)).isoformat()
    hr_hist = {}
    for a in past_acts:
        if a.date >= cut150 and facts[a.id].hist is not None:
            hr_hist[a.id] = facts[a.id].hist
    return Profile(
        hr_hist=hr_hist,
        as_of=as_of, max_hr=mx, threshold=th, aerobic=lt1, zones=zones, rest_hr=rest,
        activities=acts, wellness=wellness, session_load=session_load, daily_load=daily,
        load_days=load_days,
        response=resp.fit(wellness, load_days),
        progress=prog.build(as_of, steady, wellness, load_days, hrr, lt1.bpm),
        typical_minutes=typical, sport_mix=mix,
    )
