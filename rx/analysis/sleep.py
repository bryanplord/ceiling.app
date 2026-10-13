"""Sleep as a bank account: what you need, what you got, what you owe.

Need = your own baseline + extra after training.
  Baseline: the 75th percentile of your last 60 nights, which approximates what
  you sleep when nothing cuts it short. Clamped to 7.5–9 h.
  Training: +0 / 15 / 30 / 45 min the night after a light / normal / big / huge
  day, judged against your own 28-day average load.
Quality: a night's sleep score docks credit, because 8 restless hours don't
  restore like 8 good ones: score 80+ full credit, 70s −15 min, 60s −30 min,
  45–59 −45 min, under 45 −60 min. Score with no duration: need × score/85.
Balance: each night's (need − credited sleep), weighted so recent nights count most
  (0.85 per night back, 14 nights). Positive = debt, negative = surplus.

Why it changes training (Fullagar 2015; Van Dongen 2003; Craven 2022; Mah 2011):
sleep loss hits high-intensity and long efforts first and raises perceived effort;
debt accumulates while you feel adapted; banked sleep protects performance.
Thresholds below are Ceiling's rules built on that, stated as rules.
"""
from __future__ import annotations

import os
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

REST, LIGHT, EASY, STEADY, HARD = "rest", "light", "easy", "steady", "hard"
DECAY = 0.85
NIGHTS = 14
EFFICIENCY = 0.9          # time asleep / time in bed
TONIGHT_ADD = {REST: 0, LIGHT: 0, EASY: 15, STEADY: 20, HARD: 30}


@dataclass
class Night:
    date: str
    actual: float | None      # hours, credited for quality
    need: float
    delta: float | None       # credited − need (+ surplus, − short)
    slept: float | None = None
    score: float | None = None


def _penalty(score: float | None) -> float:
    if score is None:
        return 0.0
    return 0.0 if score >= 80 else 0.25 if score >= 70 else 0.5 if score >= 60 else 0.75 if score >= 45 else 1.0


@dataclass
class SleepBank:
    base: float
    nights: list[Night] = field(default_factory=list)
    balance: float = 0.0      # + debt (hours owed), − surplus
    state: str = "unknown"    # surplus | even | owed | debt | deep_debt | unknown
    tone: str = "none"
    cap: str = HARD
    volume: float = 1.0       # multiply session length by this
    title: str = ""
    says: str = ""
    no_technical: bool = False
    need_tonight: float = 0.0
    parts: list[str] = field(default_factory=list)
    lights_out: str = ""
    wake: str = ""


def _hm(h: float) -> str:
    sign = "−" if h < 0 else ""
    h = abs(h)
    hh, mm = int(h), int(round((h - int(h)) * 60))
    if mm == 60:
        hh, mm = hh + 1, 0
    return f"{sign}{hh}h {mm:02d}m" if hh else f"{sign}{mm} min"


def _training_add(load: float, avg: float) -> float:
    if avg <= 0 or load <= 0:
        return 0.0
    r = load / avg
    return 0.0 if r < 0.5 else 0.25 if r < 1.2 else 0.5 if r < 1.8 else 0.75


def _wake() -> tuple[int, int, str]:
    raw = os.getenv("RX_WAKE", "6:30 AM").strip().upper()
    try:
        t = datetime.strptime(raw, "%I:%M %p")
    except ValueError:
        t = datetime.strptime("6:30 AM", "%I:%M %p")
    return t.hour, t.minute, t.strftime("%-I:%M %p")


def bank(wellness, daily_load: dict[str, float], day: str, verdict: str | None = None) -> SleepBank:
    d0 = date.fromisoformat(day)
    by = {w.date: w for w in wellness}
    hist = [w.sleep_secs / 3600 for w in wellness
            if w.sleep_secs and w.date <= day and w.date > (d0 - timedelta(days=60)).isoformat()]
    scores = [w.sleep_score for w in wellness if w.sleep_score and w.date > (d0 - timedelta(days=60)).isoformat()]
    if len(hist) < 7 and len(scores) < 7:
        return SleepBank(8.0, title="Not enough sleep data", says="Need a week of sleep data to track your balance.")
    base = min(9.0, max(7.5, statistics.quantiles(hist, n=4)[2])) if len(hist) >= 7 else 8.0
    avg_load = statistics.mean([daily_load.get((d0 - timedelta(days=n)).isoformat(), 0.0) for n in range(1, 29)])

    nights: list[Night] = []
    bal = 0.0
    for k in range(NIGHTS):
        nd = (d0 - timedelta(days=k)).isoformat()             # the morning you woke up
        trained = (d0 - timedelta(days=k + 1)).isoformat()    # the day before that night
        need = base + _training_add(daily_load.get(trained, 0.0), avg_load)
        w = by.get(nd)
        slept = w.sleep_secs / 3600 if w and w.sleep_secs else None
        score = w.sleep_score if w and w.sleep_score else None
        if slept is not None:
            act = max(0.0, slept - _penalty(score))
        elif score is not None:
            act = need * min(1.0, score / 85)
        else:
            act = None
        delta = (act - need) if act is not None else None
        nights.append(Night(nd, act, need, delta, slept, score))
        if delta is not None:
            bal += -delta * DECAY ** k
    nights.reverse()
    b = SleepBank(base, nights, bal)
    last = nights[-1].slept
    last_score = nights[-1].score

    if bal >= 8:
        b.state, b.tone, b.cap, b.volume = "deep_debt", "bad", LIGHT, 0.6
        b.title = "Deep sleep debt"
        b.says = (f"You owe {_hm(bal)} of sleep. At this level training doesn't stick: you're adding fatigue "
                  f"you can't clear. Sleep is the workout until this comes down.")
    elif bal >= 5:
        b.state, b.tone, b.cap, b.volume = "debt", "bad", EASY, 0.75
        b.title = "In sleep debt"
        b.says = (f"You owe {_hm(bal)} of sleep. Hard efforts will feel harder and build less. "
                  f"Easy only, and shorter, until you've paid some back.")
    elif bal >= 2.5:
        b.state, b.tone, b.cap, b.volume = "owed", "ok", STEADY, 0.9
        b.title = "Running a sleep deficit"
        b.says = (f"You're {_hm(bal)} short over the last two weeks. Fine for steady miles; "
                  f"save the intervals until you've caught up.")
    elif bal <= -1.5:
        b.state, b.tone = "surplus", "good"
        b.title = "Sleep in the bank"
        b.says = (f"You've banked {_hm(-bal)} of sleep. Rested legs absorb hard work best, so if recovery "
                  f"agrees, this is the day for the big one.")
    else:
        b.state, b.tone = "even", "good"
        b.title = "Sleep is square"
        b.says = "You're getting the sleep you need. Sleep isn't holding you back."

    if last is not None and (last < 6.0 or last < nights[-1].need - 1.5):
        if b.cap == HARD:
            b.cap = STEADY
        b.says += f" Last night was short ({_hm(last)}), so no intervals today."
    elif last_score is not None and last_score < 50:
        if b.cap == HARD:
            b.cap = STEADY
        b.says += f" Last night scored {last_score:.0f}: you were in bed, but it didn't restore you. No intervals today."
    if last is not None and last < 5.0:
        b.no_technical = True
        b.says += " Under 5 hours, reaction time goes first: skip technical descents today."

    # Tonight
    payback = min(1.0, max(0.0, bal) / 3)
    add = TONIGHT_ADD.get(verdict or STEADY, 15) / 60
    b.need_tonight = base + add + payback
    b.parts = [f"{_hm(base)} your baseline"]
    if add:
        b.parts.append(f"{_hm(add)} for today's session")
    if payback >= 0.1:
        b.parts.append(f"{_hm(payback)} toward what you owe")
    hh, mm, b.wake = _wake()
    wake_dt = datetime(2000, 1, 2, hh, mm)
    lo = wake_dt - timedelta(hours=b.need_tonight / EFFICIENCY)
    lo = lo - timedelta(minutes=lo.minute % 5)
    b.lights_out = lo.strftime("%-I:%M %p")
    return b


def tomorrow_if(wellness, daily_load: dict[str, float], day: str, slept: float) -> str:
    """The sleep cap tomorrow if you sleep `slept` hours tonight."""
    from ..store import Wellness
    nd = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
    future = [w for w in wellness if w.date <= day] + [Wellness(date=nd, sleep_secs=slept * 3600)]
    return bank(future, daily_load, nd).cap


def chart(b: SleepBank, w: int = 640, h: int = 200) -> str:
    """14 nights against your need: green above the line is sleep banked, red below is owed."""
    ns = b.nights
    vals = [n.delta for n in ns if n.delta is not None]
    if len(vals) < 3:
        return ""
    m = max(1.5, max(abs(v) for v in vals))
    L, R, T, B = 8, 8, 18, h - 26
    mid = (T + B) / 2
    cw = (w - L - R) / len(ns)
    Y = lambda v: mid - (B - T) / 2 * v / m
    o = [f'<svg class="sbank" viewBox="0 0 {w} {h}" width="100%" role="img" '
         f'aria-label="Sleep versus your need, last {len(ns)} nights">']
    o.append(f'<line class="need" x1="{L}" x2="{w - R}" y1="{mid:.1f}" y2="{mid:.1f}"/>')
    for i, n in enumerate(ns):
        x = L + i * cw + cw * 0.18
        bw = cw * 0.64
        if n.delta is None:
            o.append(f'<circle class="miss" cx="{x + bw / 2:.1f}" cy="{mid:.1f}" r="3"/>')
            continue
        y = Y(n.delta)
        top, hgt = (y, mid - y) if n.delta >= 0 else (mid, y - mid)
        o.append(f'<rect class="{"plus" if n.delta >= 0 else "minus"}" x="{x:.1f}" y="{top:.1f}" '
                 f'width="{bw:.1f}" height="{max(1.5, hgt):.1f}" rx="2"/>')
        if i in (0, len(ns) - 1) or i == len(ns) // 2:
            o.append(f'<text class="tk" x="{x + bw / 2:.1f}" y="{h - 6}" text-anchor="middle">'
                     f'{date.fromisoformat(n.date).strftime("%a %-d")}</text>')
    o.append(f'<text class="tk" x="{L}" y="{T - 4}">+{m:.1f}h</text>')
    o.append(f'<text class="tk" x="{w - R}" y="{mid - 6:.1f}" text-anchor="end">your need</text>')
    o.append("</svg>")
    return "".join(o)
