"""Sleep as a bank account: what you need, what you got, what you owe.

Need = your own baseline + extra after training.
  Baseline: the 75th percentile of your last 60 nights, which approximates what
  you sleep when nothing cuts it short. Clamped to 7.5–9 h.
  Training: +0 / 15 / 30 / 45 min the night after a light / normal / big / huge
  day, judged against your own 28-day average load.
Quality: a night's sleep score docks credit, because 8 restless hours don't
  restore like 8 good ones: score 80+ full credit, 70s −15 min, 60s −30 min,
  45–59 −45 min, under 45 −60 min. Score with no duration: need × score/85.
Debt behaves like fatigue: it builds night by night and drains slowly.
  debt = yesterday's debt × e^(−1/4) + tonight's shortfall   (τ ≈ 4 days, Kitamura 2016)
  A night over your need repays only half its surplus, at most 2 h a night,
  because you can't sleep it all back at once (Belenky 2003, Banks 2010).
  With no debt, surplus is banked (up to 3 h), and it drains the same way.
  Balance = debt − bank. Positive = owed, negative = banked.

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
import math
KEEP = math.exp(-1 / 4)   # debt carried night to night (τ ≈ 4 days)
NIGHTS = 14               # shown
HISTORY = 40              # modelled
EFFICIENCY = 0.93         # time asleep / time in bed (Garmin's duration already excludes wake)
TONIGHT_ADD = {REST: 0, LIGHT: 0, EASY: 15, STEADY: 20, HARD: 30}


_ORDER = [REST, LIGHT, EASY, STEADY, HARD]


def ORDER_MIN(a: str, b: str) -> str:
    return _ORDER[min(_ORDER.index(a), _ORDER.index(b))]


@dataclass
class Night:
    date: str
    actual: float | None      # hours, credited for quality
    need: float
    delta: float | None       # credited − need (+ surplus, − short)
    slept: float | None = None
    score: float | None = None
    debt: float = 0.0         # running balance after this night (+ owed, − banked)


def _penalty(score: float | None) -> float:
    if score is None:
        return 0.0
    # Garmin's score already folds in overnight HRV/stress, which Ceiling reads directly,
    # so the score only nudges: it stands in for restlessness and wake time.
    return 0.0 if score >= 80 else 0.1 if score >= 70 else 0.25 if score >= 60 else 0.33 if score >= 45 else 0.5


@dataclass
class SleepBank:
    base: float
    nights: list[Night] = field(default_factory=list)
    history: list[Night] = field(default_factory=list)   # all modelled nights, for trends
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
    raw = os.getenv("RX_WAKE", "7:00 AM").strip().upper()
    try:
        t = datetime.strptime(raw, "%I:%M %p")
    except ValueError:
        t = datetime.strptime("7:00 AM", "%I:%M %p")
    return t.hour, t.minute, t.strftime("%-I:%M %p")


def bank(wellness, daily_load: dict[str, float], day: str, verdict: str | None = None,
         history_nights: int | None = None) -> SleepBank:
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
    for k in range(history_nights or HISTORY):
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
    nights.reverse()
    debt = banked = 0.0
    debts = []
    for n in nights:
        debt *= KEEP
        banked *= KEEP
        if n.delta is not None:
            if n.delta < 0:
                short = -n.delta
                use = min(banked, short)
                banked -= use
                debt += short - use
            else:
                repay = 0.5 * min(2.0, n.delta)
                if debt > 0:
                    debt = max(0.0, debt - repay)
                else:
                    banked = min(3.0, banked + repay)
        n.debt = debt - banked
        debts.append(debt)
    bal = debt - banked
    big_yday = avg_load > 0 and daily_load.get((d0 - timedelta(days=1)).isoformat(), 0.0) > 1.5 * avg_load
    recent_peak = max(debts[-4:-1]) if len(debts) >= 4 else 0.0
    history = nights
    nights = nights[-NIGHTS:]
    b = SleepBank(base, nights, history, bal)
    last = nights[-1].slept
    last_score = nights[-1].score

    if bal >= 8:
        b.state, b.tone, b.cap, b.volume = "deep_debt", "bad", LIGHT, 0.6
        b.title = "Deep sleep debt"
        b.says = (f"You owe {_hm(bal)} of sleep. At this level it isn't lost time, it's stress: higher "
                  f"cortisol, slower muscle repair, lower HRV. Training on top of it digs deeper. "
                  f"Add an hour in bed a night until this comes down.")
    elif bal >= 5:
        b.state, b.tone, b.cap, b.volume = "debt", "bad", EASY, 0.75
        b.title = "In sleep debt"
        b.says = (f"You owe {_hm(bal)} of sleep. That's fatigue your body is carrying on top of training: "
                  f"hard efforts feel harder and build less, and you're more likely to catch something. "
                  f"Easy only, and shorter, until it's under 3 hours.")
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
    elif bal >= 1.0:
        b.state, b.tone = "slightly_owed", "ok"
        b.title = "A little short on sleep"
        b.says = (f"You owe {_hm(bal)}. Not enough to change today, but it's the start of a pile. "
                  f"An early night clears it.")
    else:
        b.state, b.tone = "even", "good"
        b.title = "Sleep is square"
        b.says = "You're getting the sleep you need. Sleep isn't holding you back."

    short_last = last is not None and (last < 6.0 or last < nights[-1].need - 1.5)
    if short_last and big_yday:
        # Sleep loss blunts muscle repair and refuelling (Lamon 2021, Skein 2011, Donga 2010):
        # a short night after a big day is the worst pairing.
        b.cap = ORDER_MIN(b.cap, EASY)
        b.volume = min(b.volume, 0.7)
        b.says += (f" Short night ({_hm(last)}) right after a big day is the worst combination: your muscles "
                   f"repair and refuel slower on short sleep. Recovery day. Eat carbs and protein early.")
    elif short_last:
        b.cap = ORDER_MIN(b.cap, STEADY)
        b.says += (f" Last night was short ({_hm(last)}), so no intervals. Expect pace or power 5–10% lower "
                   f"at the same effort today. That's the sleep, not your fitness.")
    if bal < 5 <= recent_peak:
        b.cap = ORDER_MIN(b.cap, STEADY)
        b.says += (" Your debt is coming down, but it takes a couple of normal nights to actually recover, "
                   "even when you feel fine. Intervals wait one more day.")
    elif last_score is not None and last_score < 50:
        b.cap = ORDER_MIN(b.cap, STEADY)
        b.says += f" Last night scored {last_score:.0f}: you were in bed, but it didn't restore you. No intervals today."
    if last is not None and last < 5.0:
        b.no_technical = True
        b.says += " Under 5 hours, reaction time goes first: skip technical descents today."

    # Tonight
    # Realistic: pay back at most 45 minutes a night, and never ask for more than 9h 15m asleep.
    # Nobody falls asleep at 7:30 PM on command; debt is repaid over several nights.
    payback = min(0.75, max(0.0, bal) / 3)
    add = TONIGHT_ADD.get(verdict or STEADY, 15) / 60
    b.need_tonight = min(9.25, base + add + payback)
    payback = max(0.0, b.need_tonight - base - add)
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
