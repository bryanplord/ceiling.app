"""Goals, and the programme that gets you there.

Four kinds of goal, each turned into a programme the daily prescription and the calendar follow:

  best_shape  Get into the best shape you can by a date (or open-ended): 3 weeks building, 1 easier,
              repeated, volume rising about 5% a week, two hard days a week.
  event       Train for an event (a marathon, a race, a big ride or tour) on a date: base, build,
              peak and taper phases counted back from the day; the event's sport gradually takes over.
  weight      Lose weight to a target by a date: a safe weekly rate (never more than 1% of body
              weight a week), the calorie gap put on easy days, hard days fuelled.
  longevity   Stay fit for life: mostly easy aerobic time, one short hard session a week, strength twice.

Periodisation follows the standard model (Bompa; Issurin's block periodisation): build load for
2 to 3 weeks, absorb it for 1; specific work closest to the event; a 2-week taper that cuts volume
40 to 60% and keeps intensity (Mujika & Padilla 2003). Weekly volume rises no more than ~10%
(the 10% rule; Gabbett 2016 on load spikes).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta

KINDS = {
    "best_shape": "Get into the best shape of my life",
    "event": "Train for an event",
    "weight": "Lose weight",
    "longevity": "Stay fit for life",
}
EVENT_SPORT = {"marathon": "run", "half marathon": "run", "10k": "run", "trail race": "run",
               "ultra": "run", "gran fondo": "bike", "century ride": "bike", "mtb race": "bike",
               "ski tour": "ski", "ski race": "ski", "triathlon": "run"}
# Weeks a sensible build needs, from where a non-specialist starts.
MIN_WEEKS = {"marathon": 16, "half marathon": 10, "ultra": 20, "10k": 6, "trail race": 10,
             "gran fondo": 10, "century ride": 10, "mtb race": 8, "ski tour": 8, "ski race": 8, "triathlon": 16}


@dataclass
class Goal:
    kind: str
    target_date: str | None = None
    event: str = ""                 # "marathon", "gran fondo", ...
    sport: str = ""                 # run | bike | ski
    target: str = ""                # free text: "sub 4:00", "finish strong"
    start_weight: float | None = None
    target_weight: float | None = None
    created: str = ""


@dataclass
class Week:
    start: str
    phase: str                      # Base | Build | Peak | Taper | Recover | Race
    hours: float
    hard_days: int
    long_minutes: int | None = None  # the week's long session in the goal sport
    note: str = ""
    current: bool = False


@dataclass
class Program:
    goal: Goal
    title: str
    summary: str
    weeks: list[Week] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    days_left: int | None = None
    phase: str = ""
    this_week: Week | None = None
    nutrition: list[str] = field(default_factory=list)


def load(store) -> Goal | None:
    v = store.get_meta("goal")
    if not v:
        return None
    try:
        return Goal(**json.loads(v))
    except Exception:
        return None


def save(store, g: Goal) -> None:
    store.set_meta("goal", json.dumps(g.__dict__))


def clear(store) -> None:
    store.set_meta("goal", "")


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _recent_hours(p, day: date, sport: str | None = None, weeks: int = 4) -> float:
    cut = (day - timedelta(days=7 * weeks)).isoformat()
    secs = sum(a.moving_s or 0 for a in p.activities if cut <= a.date < day.isoformat() and a.sport != "walk"
               and (sport is None or a.sport == sport))
    return secs / 3600 / weeks


def _longest(p, day: date, sport: str, weeks: int = 6) -> int:
    cut = (day - timedelta(days=7 * weeks)).isoformat()
    m = [round((a.moving_s or 0) / 60) for a in p.activities if a.sport == sport and cut <= a.date < day.isoformat()]
    return max(m) if m else 0


def build(p, g: Goal, today: date) -> Program:
    base_h = max(3.0, _recent_hours(p, today))
    if g.kind == "event":
        return _event(p, g, today, base_h)
    if g.kind == "weight":
        return _weight(p, g, today, base_h)
    if g.kind == "longevity":
        return _longevity(p, g, today, base_h)
    return _best_shape(p, g, today, base_h)


def _cycle(start: date, n: int, base_h: float, hard: int = 2, ramp: float = 0.05) -> list[Week]:
    """3 weeks building, 1 easier. Volume rises ~5% a building week, drops ~30% on the easy one."""
    out, h = [], base_h
    for i in range(n):
        ws = start + timedelta(weeks=i)
        if i % 4 == 3:
            out.append(Week(ws.isoformat(), "Recover", round(h * 0.7, 1), 1, note="Absorb the last three weeks."))
        else:
            h = h * (1 + ramp) if i else h
            out.append(Week(ws.isoformat(), "Build", round(h, 1), hard))
    return out


def _mark(prog: Program, today: date) -> Program:
    mon = _monday(today).isoformat()
    for w in prog.weeks:
        if w.start == mon:
            w.current = True
            prog.this_week, prog.phase = w, w.phase
    return prog


def _best_shape(p, g: Goal, today: date, base_h: float) -> Program:
    end = date.fromisoformat(g.target_date) if g.target_date else today + timedelta(weeks=16)
    n = max(4, min(52, (end - _monday(today)).days // 7 + 1))
    weeks = _cycle(_monday(today), n, base_h)
    left = (end - today).days if g.target_date else None
    prog = Program(g, "The best shape of your life" + (f" by {end.strftime('%B %-d')}" if g.target_date else ""),
                   f"Three weeks building, one easier, repeated. Volume rises about 5% a building week from your "
                   f"current {base_h:.1f} hours, with two hard days a week and everything else easy.",
                   weeks, days_left=left)
    return _mark(prog, today)


def _event(p, g: Goal, today: date, base_h: float) -> Program:
    ev = (g.event or "event").lower()
    sport = g.sport or EVENT_SPORT.get(ev, "run")
    if not g.target_date:
        return Program(g, f"Train for a {ev}", "Pick a date to build the programme.", [])
    race = date.fromisoformat(g.target_date)
    weeks_to = max(1, (race - _monday(today)).days // 7 + 1)
    warnings = []
    need = MIN_WEEKS.get(ev, 10)
    runs30 = sum(1 for a in p.activities if a.sport == sport and a.date >= (today - timedelta(days=30)).isoformat())
    if sport == "run" and runs30 < 6:
        need += 4
    if weeks_to < need:
        warnings.append(f"{weeks_to} weeks is short for a {ev} from where you are: a safe build needs about "
                        f"{need}. The programme compresses base, so expect to finish rather than race it.")
    long_now = max(20, _longest(p, today, sport))
    long_goal = {"marathon": 180, "half marathon": 105, "ultra": 240, "10k": 60, "trail race": 120,
                 "gran fondo": 300, "century ride": 330, "mtb race": 180, "ski tour": 300}.get(ev, 150)
    sport_now = _recent_hours(p, today, sport)
    weeks: list[Week] = []
    taper, peak = 2, 3 if weeks_to >= 10 else 2
    build_w = max(2, round((weeks_to - taper - peak) * 0.4))
    base_w = max(0, weeks_to - taper - peak - build_w)
    h, long_m = base_h, long_now
    for i in range(weeks_to):
        ws = _monday(today) + timedelta(weeks=i)
        if ws + timedelta(days=6) >= race:
            weeks.append(Week(ws.isoformat(), "Race", round(h * 0.4, 1), 0, None, f"Race day: {race.strftime('%A')}."))
            continue
        if i < base_w:
            phase, hard = "Base", 1
        elif i < base_w + build_w:
            phase, hard = "Build", 2
        elif i < weeks_to - taper:
            phase, hard = "Peak", 2
        else:
            phase, hard = "Taper", 1
        recover = phase in ("Base", "Build") and i % 4 == 3
        if phase == "Taper":
            wk_h, wk_long, note = round(h * (0.6 if i == weeks_to - taper else 0.45), 1), round(long_m * 0.6), \
                "Less volume, same intensity: you arrive fresh, not unfit."
        elif recover:
            wk_h, wk_long, note = round(h * 0.7, 1), round(long_m * 0.8), "Easier week: absorb the work."
        else:
            h = min(h * 1.07, base_h * 1.8) if i else h
            # long session grows steadily so it reaches the event's demand in the peak weeks, not before:
            # never more than ~10% or 15 minutes a week
            grow_weeks = max(1, int((base_w + build_w) * 0.75) + (peak if phase == "Peak" else 0))
            step = (long_goal - long_now) / max(1, base_w + build_w)
            long_m = min(long_goal, long_m + max(5, min(15, round(max(step, long_m * 0.05)))))
            wk_h, wk_long = round(h, 1), long_m
            note = {"Base": f"Mostly easy, the {sport} share growing.",
                    "Build": "Add race-specific efforts: steady tempo in the long session.",
                    "Peak": "The longest sessions, at race effort in places."}[phase]
        weeks.append(Week(ws.isoformat(), phase, wk_h, hard, wk_long, note))
    days_left = (race - today).days
    title = f"{g.event.title() if g.event else 'Event'} on {race.strftime('%B %-d, %Y')}"
    summary = (f"{weeks_to} weeks: {base_w} base, {build_w} build, {peak} peak, {taper} taper. "
               f"Your {sport} volume is {sport_now:.1f} hours a week now; the long {sport} grows from "
               f"{long_now} minutes toward {long_goal}, about 10 minutes a week.")
    if g.target:
        summary += f" Target: {g.target}."
    return _mark(Program(g, title, summary, weeks, warnings, days_left), today)


def _weight(p, g: Goal, today: date, base_h: float) -> Program:
    warnings, nutrition = [], []
    cur, tgt = g.start_weight, g.target_weight
    end = date.fromisoformat(g.target_date) if g.target_date else None
    weeks = _cycle(_monday(today), 12, base_h, hard=2, ramp=0.03)
    if not (cur and tgt and end):
        return _mark(Program(g, "Lose weight", "Add your current weight, target and date.", weeks), today)
    wks = max(1, (end - today).days / 7)
    rate = (cur - tgt) / wks
    safe = cur * 0.01
    if rate > safe:
        warnings.append(f"{rate:.1f} lb a week is faster than is safe while training ({safe:.1f} lb a week, 1% of "
                        f"your weight). At a safe rate you'd reach {tgt:.0f} lb around "
                        f"{(today + timedelta(weeks=(cur - tgt) / safe)).strftime('%B %-d')}.")
        rate = safe
    deficit = round(rate * 3500 / 7 / 50) * 50
    protein = round(tgt * 0.9 / 5) * 5
    nutrition = [
        f"A daily gap of about {deficit} calories gets you {rate:.1f} lb a week.",
        "Put the gap on easy and rest days. On hard days, eat to fuel them: carbs before and after.",
        f"Protein about {protein} g a day so the weight you lose is fat, not muscle.",
        "Keep two strength sessions a week for the same reason.",
        "If HRV drops for several days while the weight is falling fast, eat more: that's underfuelling.",
    ]
    prog = Program(g, f"{cur:.0f} lb to {tgt:.0f} lb by {end.strftime('%B %-d')}",
                   f"{rate:.1f} lb a week over {wks:.0f} weeks, with training kept steady and mostly easy: "
                   f"easy hours burn fat without raising appetite the way hard ones do.",
                   weeks, warnings, (end - today).days, nutrition=nutrition)
    return _mark(prog, today)


def _longevity(p, g: Goal, today: date, base_h: float) -> Program:
    weeks = _cycle(_monday(today), 12, max(base_h, 4.0), hard=1, ramp=0.03)
    for w in weeks:
        w.note = (w.note + " " if w.note else "") + "Two strength sessions; one short hard session."
    return _mark(Program(g, "Fit for life",
                         "Mostly Aerobic-zone time, one short hard session a week for VO2max, and strength twice "
                         "a week. The mix the research links most strongly with a long, healthy life.",
                         weeks), today)


def daily(prog: Program | None, day: date) -> dict:
    """What the programme asks of a given day: the cap on hard days, the long session, the sport."""
    if not prog or not prog.weeks:
        return {}
    mon = _monday(day).isoformat()
    w = next((x for x in prog.weeks if x.start == mon), None)
    if not w:
        return {}
    g = prog.goal
    sport = g.sport or EVENT_SPORT.get((g.event or "").lower(), "") if g.kind == "event" else ""
    long_day = w.long_minutes and day.weekday() == 6        # the long session goes on Sunday
    return {"phase": w.phase, "hard_days": w.hard_days, "sport": sport,
            "long_minutes": w.long_minutes if long_day else None, "week": w,
            "race_day": g.kind == "event" and g.target_date == day.isoformat()}


CANNED = [
    {"id": "best", "title": "Get into the best shape of my life", "kind": "best_shape", "weeks": 16},
    {"id": "marathon", "title": "Run a marathon", "kind": "event", "event": "marathon", "weeks": 20},
    {"id": "half", "title": "Run a half marathon", "kind": "event", "event": "half marathon", "weeks": 12},
    {"id": "fondo", "title": "Ride a gran fondo", "kind": "event", "event": "gran fondo", "weeks": 12},
    {"id": "mtb", "title": "Race mountain bikes", "kind": "event", "event": "mtb race", "weeks": 10},
    {"id": "tour", "title": "Big ski-touring season", "kind": "event", "event": "ski tour", "weeks": 10},
    {"id": "weight", "title": "Lose weight", "kind": "weight", "weeks": 12},
    {"id": "life", "title": "Stay fit for life", "kind": "longevity", "weeks": None},
]

MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                       "september", "october", "november", "december"], 1)}
MONTHS.update({k[:3]: v for k, v in list(MONTHS.items())})


def parse_text(text: str, today: date, weight_now: float | None = None) -> Goal:
    """'run a marathon in May', 'lose 15 lb by Christmas', 'best shape by March 1', 'gran fondo June 7'."""
    import re
    t = text.lower()
    d = None
    m = re.search(r"\b(" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\b\.?\s*(\d{1,2})?(?:st|nd|rd|th)?(?:,?\s*(\d{4}))?", t)
    if m:
        mon = MONTHS[m.group(1)]
        yr = int(m.group(3)) if m.group(3) else (today.year if mon > today.month or (mon == today.month and m.group(2)) else today.year + 1)
        day_ = int(m.group(2)) if m.group(2) else 15
        try:
            d = date(yr, mon, min(day_, 28 if mon == 2 else 30 if mon in (4, 6, 9, 11) else 31))
        except ValueError:
            d = None
    if "christmas" in t:
        d = date(today.year if today < date(today.year, 12, 25) else today.year + 1, 12, 25)
    mw = re.search(r"(\d+)\s*(?:weeks|wks)", t)
    if mw and not d:
        d = today + timedelta(weeks=int(mw.group(1)))
    md = re.search(r"(\d+)\s*months?", t)
    if md and not d:
        d = today + timedelta(days=30 * int(md.group(1)))
    iso = d.isoformat() if d else None
    lose = re.search(r"(?:lose|drop|shed)\s+(\d+)\s*(?:lb|lbs|pounds)", t)
    to = re.search(r"(?:to|get to|down to|reach)\s+(\d{2,3})\s*(?:lb|lbs|pounds)?", t)
    if lose or "weight" in t or "lbs" in t or "pounds" in t:
        tgt = float(to.group(1)) if to else (weight_now - float(lose.group(1)) if (lose and weight_now) else None)
        return Goal("weight", iso or (today + timedelta(weeks=12)).isoformat(), target=text.strip()[:120],
                    start_weight=weight_now, target_weight=tgt, created=today.isoformat())
    for ev in sorted(EVENT_SPORT, key=len, reverse=True):
        if ev in t:
            return Goal("event", iso, event=ev, sport=EVENT_SPORT[ev], target=text.strip()[:120], created=today.isoformat())
    if "race" in t or "event" in t:
        sport = "bike" if any(w in t for w in ("bike", "ride", "cycling", "mtb")) else "ski" if "ski" in t else "run"
        return Goal("event", iso, event="race", sport=sport, target=text.strip()[:120], created=today.isoformat())
    if any(w in t for w in ("longevity", "for life", "healthy", "live long")):
        return Goal("longevity", iso, target=text.strip()[:120], created=today.isoformat())
    return Goal("best_shape", iso, target=text.strip()[:120], created=today.isoformat())
