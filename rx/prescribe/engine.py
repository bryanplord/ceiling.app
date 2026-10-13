"""The prescription engine. Deterministic: same data in, same prescription out.

Five parts, always: Verdict, Why, Do, Don't, Tonight. Plus if/then scenarios.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from ..analysis import load as la
from ..analysis.recovery import AMBER, GREEN, RED, Recovery, assess
from ..profile import Profile
from ..sports import BIKE, LABEL, RUN, SKI, WALK

REST, LIGHT, EASY, STEADY, HARD = "rest", "light", "easy", "steady", "hard"
ORDER = [REST, LIGHT, EASY, STEADY, HARD]
HEADLINE = {
    REST: "Rest day.",
    LIGHT: "Light day.",
    EASY: "Easy day.",
    STEADY: "Go. Steady.",
    HARD: "Go hard.",
}
TOMORROW = {
    REST: "a rest day",
    LIGHT: "a light day, walking only",
    EASY: "an easy day",
    STEADY: "a steady day: longer allowed, no intervals",
    HARD: "green: go long or go hard",
}
WALK_MPH = 3.3
EARLY_BED = "In bed by 9:30 PM."


@dataclass
class Rx:
    date: str
    verdict: str
    headline: str
    why: list[str]
    do: str
    dont: str
    tonight: str
    sport: str
    hr_low: int | None
    hr_ceiling: int | None
    max_minutes: int | None
    steps: list[str] = field(default_factory=list)
    scenarios: list[str] = field(default_factory=list)
    recovery: Recovery | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class Inputs:
    """Everything the decision depends on; separated so tests and scenarios can build it directly."""
    recovery: Recovery
    yesterday: la.LoadDay | None
    last_two: list[float]


def decide(i: Inputs) -> tuple[str, list[str]]:
    """Return (verdict, reasons). The reasons are the 'Why' line, strongest first."""
    rec, y = i.recovery, i.yesterday
    reasons: list[str] = []
    ctl = y.ctl if y else 0.0
    ratio = y.ratio if y else 1.0
    big_yday = bool(y and ctl > 5 and y.load > 1.5 * ctl)
    hard_recent = any(v > 1.8 * ctl for v in i.last_two) if ctl > 5 else False

    for s in rec.signals:
        if s.status in (RED, AMBER):
            reasons.append(f"{s.name}: {s.detail}")
    if ctl > 5 and abs(ratio - 1) >= 0.15:
        pct = round((ratio - 1) * 100)
        reasons.append(f"Last 7 days of load are {abs(pct)}% {'above' if pct > 0 else 'below'} your 6-week norm.")
    if big_yday:
        reasons.append("Yesterday was a big day.")

    if rec.reds >= 2:
        v = REST
    elif rec.reds == 1 or ratio > 1.5:
        v = LIGHT
    elif rec.ambers >= 2 or ratio > 1.3:
        v = EASY
    elif rec.known == 0:
        v = EASY
        reasons.append("No sleep or HRV data for last night, so today stays conservative.")
    elif rec.ambers == 1:
        v = STEADY
    elif big_yday or hard_recent or ratio > 1.15:
        v = STEADY
        reasons.insert(0, "Recovery looks good: you're absorbing the recent work.")
    else:
        v = HARD
        reasons.insert(0, "Recovered, and nothing hard in the last two days.")
    if not reasons:
        reasons.append("Sleep, HRV and resting HR are all in your normal range.")
    return v, reasons


def _pick_sport(verdict: str, p: Profile) -> str:
    if verdict in (REST, LIGHT):
        return WALK
    for s in p.sport_mix:
        if s in (BIKE, RUN, SKI):
            return s
    return BIKE


def _minutes(verdict: str, sport: str, p: Profile) -> int:
    typical = p.typical_minutes.get(sport, 75)
    if verdict == REST:
        return 40
    if verdict == LIGHT:
        return 60
    if verdict == EASY:
        return int(min(75, max(45, typical * 0.8)))
    if verdict == STEADY:
        return int(min(150, max(60, typical)))
    return 75


def _round5(m: float) -> int:
    return int(5 * round(m / 5))


def build_rx(p: Profile, day: str | None = None) -> Rx:
    day = day or p.as_of
    rec = assess(p.wellness, day)
    y = p.yesterday
    last_two = [ld.load for ld in p.load_days[-2:]]
    verdict, why = decide(Inputs(rec, y, last_two))
    sport = _pick_sport(verdict, p)
    mins = _round5(_minutes(verdict, sport, p))

    if not p.ready:
        return Rx(day, verdict, HEADLINE[verdict], why,
                  do=f"{LABEL[sport]}, {mins} min max, conversational effort.",
                  dont="No hard efforts until your heart-rate profile is built.",
                  tonight=EARLY_BED if rec.sleep.status in (RED, AMBER) else "Normal bedtime.",
                  sport=sport, hr_low=None, hr_ceiling=None, max_minutes=mins, recovery=rec,
                  notes=["Not enough hard efforts yet to set your heart-rate ranges. "
                         "Sync more history or upload the Garmin export."])

    z = p.zones
    steps: list[str] = []
    if verdict in (REST, LIGHT):
        ceiling = z.top("Z1 Recovery")
        low = None
        miles = round(mins / 60 * WALK_MPH, 1)
        do = f"Walk, {mins} min or {miles} miles max. Keep HR under {ceiling}."
        dont = "No running. No riding. No intervals."
        if verdict == REST:
            dont = "No training. A walk is the whole day."
    elif verdict == EASY:
        low, ceiling = z.top("Z1 Recovery") - 10, z.bottom("Z2 Aerobic") + 6
        do = f"{LABEL[sport]}, HR {low}–{ceiling}. Never above {ceiling}. {mins} minutes max."
        dont = f"No climbs that push you past {ceiling}. Walk or soft-pedal them."
    elif verdict == STEADY:
        low, ceiling = z.bottom("Z2 Aerobic"), z.top("Z2 Aerobic")
        do = f"{LABEL[sport]}, HR {low}–{ceiling}. Never above {ceiling}. {mins} minutes max — no longer."
        dont = f"No efforts above {ceiling}. No intervals today."
    else:  # HARD
        z4lo, z4hi = z.bottom("Z4 Threshold"), z.top("Z4 Threshold")
        low, ceiling = z4lo, z4hi + 3
        steps = [
            f"15 min easy, HR under {z.top('Z2 Aerobic')}.",
            f"4 × 8 min at HR {z4lo}–{z4hi}. Recover 4 min easy between.",
            f"10 min easy to finish.",
        ]
        do = f"{LABEL[sport]}: 4 × 8 min hard at HR {z4lo}–{z4hi}, 75 minutes total."
        dont = f"Don't go above {ceiling}, even on the last rep. Don't add a fifth."

    tonight = EARLY_BED if (rec.sleep.status in (RED, AMBER) or verdict in (REST, LIGHT, HARD)) \
        else "Normal bedtime. Aim for 8 hours in bed."
    rx = Rx(day, verdict, HEADLINE[verdict], why[:3], do, dont, tonight, sport,
            low, ceiling, mins, steps, recovery=rec)
    rx.scenarios = scenarios(p, rx)
    if not p.max_hr.confirmed and p.max_hr.bpm:
        rx.notes.append(f"Max HR {p.max_hr.bpm} is from a single day; ranges firm up as it's matched.")
    return rx


def _est_load(p: Profile, minutes: float, avg_hr: float) -> float:
    return la.trimp_from_summary(minutes, avg_hr, p.rest_hr, p.max_bpm)


def scenarios(p: Profile, rx: Rx) -> list[str]:
    """If you do X today, then tomorrow (assuming a normal night) is Y."""
    if not p.ready or not p.yesterday:
        return []
    z = p.zones
    options = [
        ("walk", f"just walk today", _est_load(p, 45, z.top("Z1 Recovery") - 15)),
        ("rx", "follow today's prescription",
         _est_load(p, rx.max_minutes or 60, ((rx.hr_low or rx.hr_ceiling - 15) + rx.hr_ceiling) / 2)),
        ("big", f"go long and hard instead (3 hours)", _est_load(p, 180, z.bottom("Z3 Tempo") + 2)),
    ]
    good_night = Recovery(rx.date, *[_assume_green(s) for s in rx.recovery.signals])
    tomorrow = (date.fromisoformat(rx.date) + timedelta(days=1)).isoformat()
    out, seen = [], set()
    for key, label, load in options:
        if key == "walk" and rx.verdict in (REST, LIGHT):
            continue
        y2 = la.step(p.yesterday, load, rx.date)
        v, _ = decide(Inputs(good_night, y2, [p.yesterday.load, load]))
        if (key, v) in seen:
            continue
        seen.add((key, v))
        out.append(f"If you {label}, tomorrow is {TOMORROW[v]}, provided you sleep well.")
    return out


def _assume_green(s):
    from ..analysis.recovery import Signal
    return Signal(s.name, GREEN, s.today, s.baseline, "assumed normal")
