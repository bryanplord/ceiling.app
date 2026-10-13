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
    LIGHT: "a walk-only day",
    EASY: "an easy day",
    STEADY: "a steady day with no intervals",
    HARD: "a green light for long or hard",
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
    compress: str | None = None
    stretch: str | None = None
    if_skip: str | None = None
    if_overdo: str | None = None
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
        do = f"{LABEL[sport]}, {mins} minutes max. HR {low}–{ceiling}, average about {round((low + ceiling) / 2)}. Never above {ceiling}."
        dont = f"No climbs that push you past {ceiling}. Walk or soft-pedal them."
    elif verdict == STEADY:
        low, ceiling = z.bottom("Z2 Aerobic"), z.top("Z2 Aerobic")
        do = f"{LABEL[sport]}, {mins} minutes max. HR {low}–{ceiling}, average about {round((low + ceiling) / 2)}. Never above {ceiling}."
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

    if sport in (BIKE, SKI) and verdict in (EASY, STEADY):
        # Mountain riding and skinning are intermittent: climbs spike HR, descents drop it.
        # The ceiling is about what you sustain, not a single beep on a punchy pitch.
        dont += (f" On climbs, judge it on the last minute, not one beep: a spike past {ceiling} for under "
                 f"30 seconds is fine. Stay above it for a minute and you're doing a harder day than this.")
    tonight = EARLY_BED if (rec.sleep.status in (RED, AMBER) or verdict in (REST, LIGHT, HARD)) \
        else "Normal bedtime. Aim for 8 hours in bed."
    rx = Rx(day, verdict, HEADLINE[verdict], why[:3], do, dont, tonight, sport,
            low, ceiling, mins, steps, recovery=rec)
    rx.scenarios = scenarios(p, rx)
    rx.if_skip, rx.if_overdo = consequences(p, rx)
    rx.compress, rx.stretch = flex(p, rx)
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


def _rx_load(p: Profile, rx: Rx) -> float:
    if rx.verdict == HARD:
        z = p.zones
        return (_est_load(p, 32, (z.bottom("Z4 Threshold") + z.top("Z4 Threshold")) / 2)
                + _est_load(p, 43, z.top("Z1 Recovery")))
    mid = ((rx.hr_low if rx.hr_low is not None else rx.hr_ceiling - 15) + rx.hr_ceiling) / 2
    return _est_load(p, rx.max_minutes or 60, mid)


def _next_verdict(p: Profile, rx: Rx, load: float) -> str:
    good_night = Recovery(rx.date, *[_assume_green(s) for s in rx.recovery.signals])
    y2 = la.step(p.yesterday, load, rx.date)
    v, _ = decide(Inputs(good_night, y2, [p.yesterday.load, load]))
    return v


def _personal_cost(p: Profile, load: float) -> str:
    r = p.response
    y = p.yesterday
    rel = load / y.ctl if y and y.ctl > 1 else None
    parts = []
    if r.fitted and rel is not None:
        pct = r.hrv_change_pct(rel)
        if pct is not None and pct < -2:
            parts.append(f"A day that size has cost you about {abs(pct):.0f}% of HRV the next morning")
    if r.recovery_days is not None and r.big_days >= 3:
        d = round(r.recovery_days)
        if d == 0:
            parts.append("after your biggest days, HRV has usually been back to normal by the next morning")
        else:
            parts.append(f"after your biggest days, HRV has taken {d + 1} mornings to get back to normal")
    if not parts:
        return "Your personal recovery cost is learned after about 3 weeks of sleep and HRV data."
    s = "; ".join(parts) + "."
    return s[0].upper() + s[1:]


def flex(p: Profile, rx: Rx) -> tuple[str | None, str | None]:
    """Compress: the shortest version that still does the job.
    Stretch: the most you can add before tomorrow gets worse than the ideal leaves it."""
    if not p.ready or not p.yesterday:
        return None, None
    z, ceil = p.zones, rx.hr_ceiling
    if rx.verdict in (REST, LIGHT):
        top = rx.max_minutes + 30
        return (f"15 minutes of walking still counts. Keep HR under {ceil}.",
                f"Walk up to {top} minutes, as long as HR stays under {ceil}. "
                f"The moment it's a workout, it stops being recovery.")
    if rx.verdict == HARD:
        z4lo, z4hi = z.bottom("Z4 Threshold"), z.top("Z4 Threshold")
        three = _est_load(p, 24, (z4lo + z4hi) / 2) + _est_load(p, 36, z.top("Z1 Recovery"))
        five = _est_load(p, 40, (z4lo + z4hi) / 2) + _est_load(p, 50, z.top("Z1 Recovery"))
        ok5 = ORDER.index(_next_verdict(p, rx, five)) >= ORDER.index(_next_verdict(p, rx, _rx_load(p, rx)))
        stretch = (f"A fifth 8-minute rep is fine. Tomorrow stays the same."
                   if ok5 else
                   f"Don't add reps. A fifth one changes tomorrow to {TOMORROW[_next_verdict(p, rx, five)]}.")
        return (f"3 × 8 min at {z4lo}–{z4hi}, 60 minutes total, still gets most of the benefit.",
                stretch)

    mid = ((rx.hr_low or ceil - 15) + ceil) / 2
    ideal_next = ORDER.index(_next_verdict(p, rx, _rx_load(p, rx)))
    most = rx.max_minutes
    for m in range(rx.max_minutes + 15, rx.max_minutes * 2 + 1, 15):
        if ORDER.index(_next_verdict(p, rx, _est_load(p, m, mid))) < ideal_next:
            break
        most = m
    shortest = max(30, _round5(rx.max_minutes * 0.5))
    compress = (f"{shortest} minutes at the same HR {rx.hr_low}–{ceil} still counts. "
                f"Don't make up for it by going harder.")
    if most > rx.max_minutes:
        stretch = (f"You can go to {most} minutes, ceiling unchanged at {ceil}, "
                   f"without changing tomorrow.")
    else:
        stretch = (f"Don't stretch it. Even 15 more minutes changes tomorrow. "
                   f"{rx.max_minutes} minutes is the most today can absorb.")
    return compress, stretch


def consequences(p: Profile, rx: Rx) -> tuple[str | None, str | None]:
    """The price of not following it: skipping it, or doing too much."""
    if not p.ready or not p.yesterday:
        return None, None
    y, z = p.yesterday, p.zones
    rx_load = _rx_load(p, rx)

    # Doing too much: 50% longer, and drifting 8 bpm over the ceiling.
    over_min = int((rx.max_minutes or 60) * 1.5)
    over_load = _est_load(p, over_min, rx.hr_ceiling + 8)
    if rx.verdict in (REST, LIGHT):
        sport = p.sport_mix[0] if p.sport_mix and p.sport_mix[0] != WALK else BIKE
        over_load = _est_load(p, p.typical_minutes.get(sport, 75), z.bottom("Z3 Tempo"))
        lead = f"Train anyway (a normal {LABEL[sport].lower()}) and"
    else:
        lead = f"Go above {rx.hr_ceiling} or past {rx.max_minutes} minutes and"
    nv = _next_verdict(p, rx, over_load)
    ideal_nv = _next_verdict(p, rx, rx_load)
    if ORDER.index(nv) < ORDER.index(ideal_nv):
        effect = f"tomorrow drops from {TOMORROW[ideal_nv]} to {TOMORROW[nv]}, even if you sleep well."
    else:
        effect = f"tomorrow stays {TOMORROW[nv]}, but only if you sleep well."
    if_overdo = f"{lead} {effect} " + _personal_cost(p, over_load)
    if rx.verdict in (REST, LIGHT):
        if_overdo += " You don't get fitter on a day you can't absorb. You dig the hole deeper."

    if rx.verdict in (REST, LIGHT):
        return "Skipping the walk costs nothing. Moving at all is the point.", if_overdo

    # Skipping: fitness lost today, and if it becomes a habit.
    ctl_rx = la.step(y, rx_load, rx.date).ctl
    ctl_skip = la.step(y, 0.0, rx.date).ctl
    lost_today = (ctl_rx - ctl_skip) / max(ctl_rx, 1) * 100
    ld = y
    for i in range(4):
        ld = la.step(ld, 0.0, rx.date)
    lost_4 = (y.ctl - ld.ctl) / max(y.ctl, 1) * 100
    nv_skip = _next_verdict(p, rx, 0.0)
    if_skip = (f"You give up about {lost_today:.1f}% of your 6-week fitness today, "
               f"and {lost_4:.0f}% if you skip the next 4 days too. "
               f"You'd be fresher tomorrow ({TOMORROW[nv_skip]}), but that's a trade, not a win.")
    return if_skip, if_overdo


def _assume_green(s):
    from ..analysis.recovery import Signal
    return Signal(s.name, GREEN, s.today, s.baseline, "assumed normal")
