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
import re
import secrets
from dataclasses import dataclass, field
from datetime import date, timedelta

from .clock import local_today

KINDS = {
    "best_shape": "Get into the best shape of my life",
    "event": "Train for an event",
    "weight": "Lose weight",
    "longevity": "Stay fit for life",
    "strength": "Get stronger",
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
    # A plan is a goal you can draft, commit to and stop. Only committed plans drive Today and the calendar.
    id: str = ""
    status: str = "draft"           # draft | active | stopped
    committed: str | None = None    # the day you committed
    stopped_on: str | None = None   # from this day on the plan contributes nothing; what you did stays
    max_hours: float | None = None  # weekly hours cap
    no_days: list = field(default_factory=list)   # weekdays you can't train, 0 = Monday
    lift_days: int | None = None    # strength plans: lifts a week
    long_day: int | None = None     # weekday for the long session; None picks Sunday, or the latest free day


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
    pace: str = ""                  # goal pace a mile, when the target is a finish time
    long_day: int = 6               # weekday of the long session (Sunday unless you can't train then)


def _from(d: dict) -> Goal | None:
    try:
        known = {k: v for k, v in d.items() if k in Goal.__dataclass_fields__}
        g = Goal(**known)
        g.no_days = [int(x) for x in (g.no_days or []) if str(x).isdigit() and 0 <= int(x) <= 6]
        return g
    except Exception:
        return None


def _write(store, plans: list[Goal]) -> None:
    store.set_meta("plans", json.dumps([g.__dict__ for g in plans]))


def all_plans(store) -> list[Goal]:
    """Every plan: drafts, active and stopped. The old single "goal" becomes one active plan."""
    raw = store.get_meta("plans")
    if raw is None:
        plans: list[Goal] = []
        old = store.get_meta("goal")
        if old:
            try:
                g = _from(json.loads(old))
            except Exception:
                g = None
            if g:
                g.id = g.id or secrets.token_hex(4)
                g.status, g.committed = "active", g.committed or g.created or local_today().isoformat()
                plans.append(g)
        _write(store, plans)
        if old:
            store.set_meta("goal", "")
        return plans
    try:
        return [g for g in (_from(d) for d in json.loads(raw or "[]")) if g]
    except Exception:
        return []


def get(store, pid: str) -> Goal | None:
    return next((g for g in all_plans(store) if g.id == pid), None)


def is_live(g: Goal, day: date) -> bool:
    """Committed on or before the day, and not stopped on or before it.

    A commit stamped the day after counts: older saves used the server's UTC date, which is already
    tomorrow after 5:00 PM Pacific, and a plan you just committed must not vanish for the evening."""
    d = day.isoformat()
    if g.status not in ("active", "stopped") or not g.committed:
        return False
    if g.committed > (day + timedelta(days=1)).isoformat():
        return False
    return not (g.status == "stopped" and g.stopped_on and g.stopped_on <= d)


def active(store, day: date) -> list[Goal]:
    return [g for g in all_plans(store) if is_live(g, day)]


def save_plan(store, g: Goal) -> Goal:
    """Add or replace by id. A new plan starts as a draft."""
    plans = all_plans(store)
    if not g.id:
        g.id = secrets.token_hex(4)
    g.created = g.created or local_today().isoformat()
    for i, x in enumerate(plans):
        if x.id == g.id:
            plans[i] = g
            break
    else:
        plans.append(g)
    _write(store, plans)
    return g


def commit(store, pid: str, day: date) -> Goal | None:
    g = get(store, pid)
    if g:
        g.status, g.committed, g.stopped_on = "active", day.isoformat(), None
        save_plan(store, g)
    return g


def stop(store, pid: str, day: date) -> Goal | None:
    """Stop from this day on. The record stays; past days keep what you actually did."""
    g = get(store, pid)
    if g and g.status == "active":
        g.status, g.stopped_on = "stopped", day.isoformat()
        save_plan(store, g)
    return g


def delete(store, pid: str) -> None:
    _write(store, [g for g in all_plans(store) if g.id != pid])


def load(store) -> Goal | None:
    """The first active plan (kept for older callers)."""
    act = active(store, local_today())
    return act[0] if act else None


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _recent_hours(p, day: date, sport: str | None = None, weeks: int = 4) -> float:
    cut = (day - timedelta(days=7 * weeks)).isoformat()
    secs = sum(a.moving_s or 0 for a in p.activities if cut <= a.date < day.isoformat() and a.sport != "walk"
               and (sport is None or a.sport == sport))
    return secs / 3600 / weeks


def baseline_hours(p, day: date) -> float:
    """Your usual training week, every sport but walks: the last 4 weeks, or 85% of the last 12 if
    the last month was unusually quiet (a trip, a cold, a wet spell), never under 3 hours."""
    return max(3.0, _recent_hours(p, day), 0.85 * _recent_hours(p, day, weeks=12))


def _longest(p, day: date, sport: str, weeks: int = 6) -> int:
    cut = (day - timedelta(days=7 * weeks)).isoformat()
    m = [round((a.moving_s or 0) / 60) for a in p.activities if a.sport == sport and cut <= a.date < day.isoformat()]
    return max(m) if m else 0


DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
DIST_MI = {"marathon": 26.219, "half marathon": 13.109, "10k": 6.214}
_SANE = {"marathon": (7200, 25200), "half marathon": (3600, 14400), "10k": (1500, 7200)}


def target_seconds(g: Goal) -> int | None:
    """A finish time in the target: '4:20' or 'sub 4:20' or '4 hours' for a marathon, '45:00' for a 10k."""
    ev = (g.event or "").lower()
    if g.kind != "event" or ev not in DIST_MI or not g.target:
        return None
    t = g.target.lower()
    m = re.search(r"\b(\d{1,2}):(\d{2})(?::(\d{2}))?\b", t)
    secs = None
    if m:
        a, b, c = int(m.group(1)), int(m.group(2)), m.group(3)
        if c is not None:
            secs = a * 3600 + b * 60 + int(c)
        elif ev == "10k" and a >= 20:
            secs = a * 60 + b
        else:
            secs = a * 3600 + b * 60
    else:
        m = re.search(r"(\d(?:\.\d+)?)\s*(?:h\b|hr\b|hrs\b|hours?\b)", t)
        if m:
            secs = round(float(m.group(1)) * 3600)
    lo, hi = _SANE[ev]
    return secs if secs and lo <= secs <= hi else None


def fmt_time(secs: int) -> str:
    h, r = divmod(int(secs), 3600)
    return f"{h}:{r // 60:02d}" if h else f"{r // 60}:{r % 60:02d}"


def pace_per_mile(g: Goal) -> str:
    secs = target_seconds(g)
    if not secs:
        return ""
    p_ = round(secs / DIST_MI[(g.event or "").lower()])
    return f"{p_ // 60}:{p_ % 60:02d}"


def long_weekday(g: Goal) -> int:
    if g.long_day is not None and 0 <= int(g.long_day) <= 6 and g.long_day not in (g.no_days or []):
        return int(g.long_day)
    for wd in (6, 5, 4, 3, 2, 1, 0):
        if wd not in (g.no_days or []):
            return wd
    return 6


def _days_text(days: list[int]) -> str:
    names = [DAY_NAMES[d] + "s" for d in sorted(days)]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def build(p, g: Goal, today: date) -> Program:
    base_h = baseline_hours(p, today)        # total training, all sports: the plan's sport grows inside it
    if g.kind == "event":
        prog = _event(p, g, today, base_h)
    elif g.kind == "weight":
        prog = _weight(p, g, today, base_h)
    elif g.kind == "longevity":
        prog = _longevity(p, g, today, base_h)
    elif g.kind == "strength":
        prog = _strength(p, g, today, base_h)
    else:
        prog = _best_shape(p, g, today, base_h)
    if g.max_hours:
        cut = False
        for w in prog.weeks:
            if w.hours > g.max_hours:
                w.hours, cut = round(float(g.max_hours), 1), True
        if cut:
            prog.summary += f" Capped at {g.max_hours:g} hours a week."
    prog.long_day = long_weekday(g)
    if g.no_days:
        prog.summary += f" No training on {_days_text(g.no_days)}."
        if prog.long_day != 6 and g.long_day is None and any(w.long_minutes for w in prog.weeks):
            prog.summary += f" The long session moves to {DAY_NAMES[prog.long_day]}."
    if g.long_day is not None and prog.long_day == g.long_day and any(w.long_minutes for w in prog.weeks):
        prog.summary += f" Long sessions on {DAY_NAMES[prog.long_day]}s."
    return prog


def progress(g: Goal, day: date) -> tuple[int, int | None]:
    """Week x of y, counted from the week you committed (or today, for a draft)."""
    start = _monday(date.fromisoformat(g.committed)) if g.committed else _monday(day)
    if g.stopped_on:
        day = min(day, date.fromisoformat(g.stopped_on))
    x = max(1, (_monday(day) - start).days // 7 + 1)
    y = None
    if g.target_date:
        y = max(x, (_monday(date.fromisoformat(g.target_date)) - start).days // 7 + 1)
    return x, y


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
    pace = pace_per_mile(g)
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
                    "Build": (f"Last 20 minutes of the long run at {pace} a mile." if pace
                              else "Add race-specific efforts: steady tempo in the long session."),
                    "Peak": (f"The longest runs, with 30 to 45 minutes at {pace} a mile." if pace
                             else "The longest sessions, at race effort in places.")}[phase]
        weeks.append(Week(ws.isoformat(), phase, wk_h, hard, wk_long, note))
    days_left = (race - today).days
    name = g.event[:1].upper() + g.event[1:] if g.event else "Event"
    tsec = target_seconds(g)
    title = f"{name} in {fmt_time(tsec)}" if tsec else name
    title += f" on {race.strftime('%B %-d, %Y')}"
    summary = (f"{weeks_to} weeks: {base_w} base, {build_w} build, {peak} peak, {taper} taper. "
               f"Your {sport} volume is {sport_now:.1f} hours a week now; the long {sport} grows from "
               f"{long_now} minutes toward {long_goal}, about 10 minutes a week.")
    if pace:
        summary += f" Goal pace is {pace} a mile; the long runs practise it in the build and peak weeks."
    elif g.target:
        summary += f" Target: {g.target}."
    prog = Program(g, title, summary, weeks, warnings, days_left, pace=pace)
    return _mark(prog, today)


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


def _strength(p, g: Goal, today: date, base_h: float) -> Program:
    n = int(g.lift_days or 3)
    weeks = _cycle(_monday(today), 12, base_h, hard=2, ramp=0.0)
    for i, w in enumerate(weeks):
        w.phase = "Lift"
        w.note = f"{n} lifts" + (", heavier week" if (_monday(today) + timedelta(weeks=i)).isocalendar()[1] % 2 == 0 else ", lighter week")
    days = {1: "once", 2: "twice"}.get(n, f"{n} times")
    return _mark(Program(g, "Get stronger",
                         f"Lift {days} a week: circuits of five or six moves, a full and a short version, weights that go up "
                         "when you've earned it. Heavier and lighter weeks alternate. A bad recovery morning makes the lift "
                         "lighter; a hard ride or run tomorrow keeps heavy legs off today.", weeks), today)


def daily(prog: Program | None, day: date) -> dict:
    """What the programme asks of a given day: the cap on hard days, the long session, the sport."""
    if not prog or not prog.weeks:
        return {}
    mon = _monday(day).isoformat()
    w = next((x for x in prog.weeks if x.start == mon), None)
    if not w:
        return {}
    g = prog.goal
    sport = g.sport or (EVENT_SPORT.get((g.event or "").lower(), "") if g.kind == "event" else "")
    rest = day.weekday() in (g.no_days or [])
    long_day = w.long_minutes and day.weekday() == getattr(prog, "long_day", 6) and not rest
    return {"phase": w.phase, "hard_days": w.hard_days, "sport": sport,
            "long_minutes": w.long_minutes if long_day else None, "week": w,
            "race_day": g.kind == "event" and g.target_date == day.isoformat(),
            "rest": rest, "pace": getattr(prog, "pace", ""), "plan": g.id, "title": prog.title}


def primary(programs: list[Program] | None, day: date) -> Program | None:
    """The plan that leads when several run at once: the nearest dated event, else the nearest
    dated goal, else the first."""
    programs = [x for x in (programs or []) if x]
    if not programs:
        return None
    d = day.isoformat()
    for pick in (lambda x: x.goal.kind == "event", lambda x: True):
        dated = [x for x in programs if pick(x) and x.goal.target_date and x.goal.target_date >= d]
        if dated:
            return min(dated, key=lambda x: x.goal.target_date)
    return programs[0]


def daily_all(programs: list[Program] | Program | None, day: date) -> dict:
    """daily() across every active plan. The lead plan (see primary) sets the phase, sport, long
    session and pace; the stricter cap wins on hard days; a day any plan rules out is a rest day."""
    if isinstance(programs, Program):
        programs = [programs]
    parts = [(x, daily(x, day)) for x in (programs or []) if x]
    parts = [(x, dd) for x, dd in parts if dd]
    if not parts:
        return {}
    lead = primary([x for x, _ in parts], day)
    out = dict(next(dd for x, dd in parts if x is lead))
    out["hard_days"] = min(dd["hard_days"] for _, dd in parts)
    out["rest"] = any(dd.get("rest") for _, dd in parts)
    out["race_day"] = any(dd.get("race_day") for _, dd in parts)
    if out["rest"]:
        out["long_minutes"] = None
    for _, dd in parts:
        if not out.get("sport") and dd.get("sport"):
            out["sport"] = dd["sport"]
        if not out["rest"] and not out.get("long_minutes") and dd.get("long_minutes"):
            out["long_minutes"] = dd["long_minutes"]
    out["plans"] = [dd["plan"] for _, dd in parts]
    return out


CANNED = [
    {"id": "best", "title": "Get into the best shape of my life", "kind": "best_shape", "weeks": 16},
    {"id": "marathon", "title": "Run a marathon", "kind": "event", "event": "marathon", "weeks": 20},
    {"id": "half", "title": "Run a half marathon", "kind": "event", "event": "half marathon", "weeks": 12},
    {"id": "fondo", "title": "Ride a gran fondo", "kind": "event", "event": "gran fondo", "weeks": 12},
    {"id": "mtb", "title": "Race mountain bikes", "kind": "event", "event": "mtb race", "weeks": 10},
    {"id": "tour", "title": "Big ski-touring season", "kind": "event", "event": "ski tour", "weeks": 10},
    {"id": "weight", "title": "Lose weight", "kind": "weight", "weeks": 12},
    {"id": "life", "title": "Stay fit for life", "kind": "longevity", "weeks": None},
    {"id": "strength", "title": "Get stronger", "kind": "strength", "weeks": None},
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
    if any(w in t for w in ("strength", "stronger", "lift", "weights", "gym", "muscle", "tone", "toned")):
        n = re.search(r"(\d|one|two|three|four|five)\s*(?:x|times|days|lifts|sessions)", t)
        nd = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
        k = (int(n.group(1)) if n.group(1).isdigit() else nd[n.group(1)]) if n else 3
        return Goal("strength", iso, target=text.strip()[:120], created=today.isoformat(), lift_days=max(1, min(5, k)))
    for ev in sorted(EVENT_SPORT, key=len, reverse=True):
        if ev in t:
            return Goal("event", iso, event=ev, sport=EVENT_SPORT[ev], target=text.strip()[:120], created=today.isoformat())
    if "race" in t or "event" in t:
        sport = "bike" if any(w in t for w in ("bike", "ride", "cycling", "mtb")) else "ski" if "ski" in t else "run"
        return Goal("event", iso, event="race", sport=sport, target=text.strip()[:120], created=today.isoformat())
    if any(w in t for w in ("longevity", "for life", "healthy", "live long")):
        return Goal("longevity", iso, target=text.strip()[:120], created=today.isoformat())
    return Goal("best_shape", iso, target=text.strip()[:120], created=today.isoformat())


# ---------------------------------------------------------------------------------------------
# Changing a plan in your own words. Rule-based and private: the same sentence always does the
# same thing, nothing leaves the server, and anything it doesn't follow is said back plainly.
# ---------------------------------------------------------------------------------------------

class Changes(list):
    """The plain-English list of what changed, plus what didn't apply and whether you asked to stop."""

    def __init__(self, *a):
        super().__init__(*a)
        self.stop = False           # "I want to quit": the page asks before stopping anything
        self.notes: list[str] = []  # what it couldn't apply, and why
        self.missed: list[str] = []  # the words it didn't follow
        self.understood = False     # it followed something, even if that was already the plan


EXAMPLE = "Try “Make it a 4:20 marathon”, “No Wednesdays”, “Max 6 hours a week” or “Move the race to Feb 8”."

_N = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
      "nine": 9, "ten": 10, "a couple of": 2, "couple of": 2, "a couple": 2, "a few": 3}
_NUM = r"(\d+|a couple of|couple of|a couple|a few|an|a|one|two|three|four|five|six|seven|eight|nine|ten)"
_MON = r"(january|february|march|april|may|june|july|august|september|sept|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)"
_WDN = r"(?:mon|tues|wednes|thurs|fri|satur|sun)day"
_WD = r"\b(mon(?:day)?|tue(?:s(?:day)?)?|wed(?:s|nesday)?|thu(?:r(?:s(?:day)?)?)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)s?\b"
_WD_IDX = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
_SUB = r"(?:\b(sub|under|below|faster than|less than|break(?:ing)?)\s*-?\s*)?"

_STOP = re.compile(r"\b(quit|give up|giving up|abandon|i'?m done|i am done|"
                   r"stop\s+(?:the\s+|this\s+|my\s+)?(?:plan|it|training(?!\s+on)(?!\s+\w+days?))|"
                   r"(?:cancel|end|scrap|drop|delete|kill)\s+(?:the\s+|this\s+|my\s+)?(?:plan|it|this)\b)")
_EVENTS = [
    (r"\bhalf[\s-]*marathon\b|\b1/2\s*marathon\b|\bhalf\b(?!\s+(?:an?\s+)?(?:hour|hr|day|week|mile|the))", "half marathon"),
    (r"\b(?:full\s+)?marathon\b", "marathon"),
    (r"\b(?:50|100)\s*k\b|\b(?:50|100)[\s-]*mil(?:e|er)s?\b|\bultra(?:[\s-]*marathon)?\b", "ultra"),
    (r"\b10\s*k\b", "10k"),
    (r"\btrail\s+(?:race|run)\b", "trail race"),
    (r"\bgran\s*fondo\b|\bfondo\b", "gran fondo"),
    (r"\bcentury(?:\s+ride)?\b", "century ride"),
    (r"\b(?:mtb|mountain[\s-]*bike)\s+race\b", "mtb race"),
    (r"\bski[\s-]*tour(?:ing)?\b", "ski tour"),
    (r"\bski\s+race\b", "ski race"),
    (r"\btri(?:athlon)?\b", "triathlon"),
]
_EVENT_SAY = {"ultra": "50k ultra"}
_SPORT_W = {"run": "run", "running": "run", "ride": "bike", "riding": "bike", "bike": "bike", "biking": "bike",
            "cycling": "bike", "ski": "ski", "skiing": "ski"}
_SPORT_SAY = {"run": "running", "bike": "riding", "ski": "skiing"}
_FILLER = {"please", "thanks", "thank", "you", "ok", "okay", "also", "and", "actually", "hey", "hi", "so", "then",
           "cool", "great", "the", "a", "plan", "it"}


def _n(word: str) -> int:
    return int(word) if word.isdigit() else _N.get(word, 1)


def _blank(s: str, m) -> str:
    return s[:m.start()] + " " * (m.end() - m.start()) + s[m.end():]


def _day_say(d: date, today: date) -> str:
    out = d.strftime("%A, %b %-d")
    return out + (f", {d.year}" if d.year != today.year and (d - today).days > 300 else "")


def _mkdate(y, mo, d, today: date) -> date | None:
    try:
        x = date(int(y), mo, d) if y else date(today.year, mo, d)
    except ValueError:
        return None
    if not y and x < today:
        try:
            x = date(today.year + 1, mo, d)
        except ValueError:
            return None
    return x


def _find_date(s: str, today: date):
    """An absolute date in the clause: (date or None if invalid, match, month_only)."""
    y = lambda g: (int(g) + 2000 if g and len(g) == 2 else int(g)) if g else None
    m = re.search(r"(?:\b" + _WDN + r",?\s+)?\b" + _MON + r"\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b(?:,?\s*(\d{4}))?", s)
    if m:
        return _mkdate(y(m.group(3)), MONTHS[m.group(1)[:3]], int(m.group(2)), today), m, False
    m = re.search(r"(?:\b" + _WDN + r",?\s+)?\b(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?" + _MON + r"\b(?:,?\s*(\d{4}))?", s)
    if m:
        return _mkdate(y(m.group(3)), MONTHS[m.group(2)[:3]], int(m.group(1)), today), m, False
    m = re.search(r"(?:\b" + _WDN + r",?\s+)?\b(\d{1,2})/(\d{1,2})(?:/(\d{4}|\d{2}))?\b", s)
    if m and 1 <= int(m.group(1)) <= 12:
        return _mkdate(y(m.group(3)), int(m.group(1)), int(m.group(2)), today), m, False
    m = re.search(r"\b(?:in|by|to|until|till|for|on|around|mid|early|late|end of)\s+(?:mid[\s-]*)?" + _MON +
                  r"\b(?:,?\s*(\d{4}))?", s)
    if m:
        return _mkdate(y(m.group(2)), MONTHS[m.group(1)[:3]], 15, today), m, True
    m = re.search(r"\b(?:by\s+|at\s+|for\s+|on\s+|to\s+)?christmas\b", s)
    if m:
        return _mkdate(None, 12, 25, today), m, False
    return None, None, False


def _finish(s: str, ev: str):
    """A finish time in the clause: (seconds, sub, match)."""
    no_clock = r"(?!\s*[ap]\.?m\b)"
    m = re.search(_SUB + r"\b(\d{1,2}):(\d{2})(?::(\d{2}))?\b" + no_clock, s)
    if m:
        a, b, c = int(m.group(2)), int(m.group(3)), m.group(4)
        secs = a * 3600 + b * 60 + int(c) if c else (a * 60 + b if ev == "10k" and a >= 20 else a * 3600 + b * 60)
        return secs, bool(m.group(1)), m
    m = re.search(_SUB + r"\b(\d{1,2}(?:\.\d+)?)\s*(?:hours?|hrs?|h)(?![a-z])(?:\s*(?:and\s*)?(\d{1,2})\s*(?:minutes?|mins?|m)?(?![a-z\d]))?", s)
    if m:
        return round(float(m.group(2)) * 3600) + int(m.group(3) or 0) * 60, bool(m.group(1)), m
    m = re.search(_SUB + r"\b(\d{2,3})\s*(?:minutes?|mins?)\b", s)
    if m:
        return int(m.group(2)) * 60, bool(m.group(1)), m
    m = re.search(r"\b(sub|under|below|break(?:ing)?)\s*-?\s*(\d{1,3})\b(?!\s*(?:lbs?|pounds?|%|weeks?|days?|months?|k\b))", s)
    if m:
        n = int(m.group(2))
        return (n * 3600 if n <= 7 else n * 60), True, m
    return None, False, None


def apply_text(goal: Goal, text: str, today: date) -> tuple[Goal, Changes]:
    """Change a plan the way you'd say it: 'Make it a 4:20 marathon. I can't train Wednesdays.
    Max 6 hours a week. Move the race to February 8.' Returns a changed copy and what changed.
    Asking to quit sets changes.stop and changes nothing: the page asks before it stops a plan."""
    from dataclasses import replace
    g = replace(goal, no_days=list(goal.no_days or []))
    ch = Changes()
    raw = (text or "").replace("\u2019", "'").strip()
    if not raw:
        return g, ch
    if _STOP.search(raw.lower()):
        ch.stop = ch.understood = True
        return g, ch

    # clauses: sentences, commas, "but"; a comma before a year or a month stays inside its date
    parts = re.split(r"\.(?=\s+[A-Za-z]|\s*$)|[;!?\n]+|,(?!\s*(?:\d{4}\b|" + _MON.replace("(", "(?:", 1) + r"\b))|\s+(?:but|also|plus)\s+", raw, flags=re.I)
    clauses = [p.strip().lower() for p in parts if p and p.strip()]
    hit = [False] * len(clauses)
    old_event, set_target, same = (goal.event or "").lower(), False, False

    def say(x: str):
        if x not in ch:
            ch.append(x)

    # 1. the event first, so a finish time in the same sentence is read for the right distance
    for i, c in enumerate(clauses):
        for pat, ev in _EVENTS:
            m = re.search(pat, c)
            if not m:
                continue
            clauses[i], hit[i] = _blank(c, m), True
            if g.kind != "event" or (g.event or "").lower() != ev:
                g.kind, g.event, g.sport = "event", ev, EVENT_SPORT[ev]
                say(f"Event: {_EVENT_SAY.get(ev, ev)}")
            else:
                same = True
            break
    ev = (g.event or "").lower()

    last_pol = None
    for i, c in enumerate(clauses):
        # weekly hours cap
        m = re.search(r"\b(?:no|remove\s+the|drop\s+the|lift\s+the|take\s+off\s+the)\s+(?:hours?\s+|weekly\s+)?(?:cap|limit)\b|\bno\s+max\b", c)
        if m:
            c, hit[i] = _blank(c, m), True
            if g.max_hours:
                g.max_hours = None
                say("No weekly hours cap")
            else:
                same = True
        week = r"(?:\s*(?:a|per|/|each|every|in\s+a)\s*week|\s*weekly)"
        m = (re.search(r"\b(?:max(?:imum)?(?:\s+of)?|at\s+most|no\s+more\s+than|not\s+more\s+than|up\s+to|cap(?:\s+it)?(?:\s+at)?|"
                       r"limit(?:\s+it)?(?:\s+to)?|only(?:\s+have)?|just|less\s+than|under)\s*(\d+(?:\.\d+)?)\s*(?:hours?|hrs?|h)(?![a-z])" + week, c)
             or re.search(r"\b(?:max(?:imum)?(?:\s+of)?|at\s+most|no\s+more\s+than|not\s+more\s+than|up\s+to|cap(?:\s+it)?(?:\s+at)?|"
                          r"limit(?:\s+it)?(?:\s+to)?)\s*(\d+(?:\.\d+)?)\s*(?:hours?|hrs?|h)(?![a-z])(?:" + week + r")?", c)
             or re.search(r"\b(\d+(?:\.\d+)?)\s*(?:hours?|hrs?|h)(?![a-z])" + week + r"(?:\s*(?:max|tops|at\s+most))?", c))
        if m:
            c, hit[i] = _blank(c, m), True
            h = float(m.group(1))
            if not 1 <= h <= 40:
                ch.notes.append(f"{h:g} hours a week is outside what the plan can use (1 to 40), so the cap stays as it was.")
            elif g.max_hours != h:
                g.max_hours = h
                say(f"At most {h:g} hours a week")
            else:
                same = True

        # weight
        lb = r"\s*(?:lbs?|pounds?)\b"
        wm = [(k, re.search(p, c)) for k, p in (
            ("lose", r"\b(?:lose|drop|shed|cut)\s+(?:another\s+)?(\d{1,3}(?:\.\d)?)\s*(?:more\s+)?(?:lbs?|pounds?)\b"),
            ("now", r"\b(?:i\s+weigh|weigh(?:ing)?|weight\s+(?:is\s+)?now|i'?m\s+now|i\s+am\s+now)\s+(?:about\s+|at\s+)?(\d{2,3}(?:\.\d)?)(?:" + lb + r")?|\bi'?m\s+(\d{2,3}(?:\.\d)?)" + lb + r"(?:\s+now)?"),
            ("to", r"\b(?:target(?:\s+weight)?(?:\s+(?:of|to|is|at))?|goal(?:\s+weight)?(?:\s+(?:of|is))?|get\s+(?:down\s+)?to|down\s+to|reach|"
                   r"weigh\s+in\s+at|aim\s+for)\s*:?\s*(\d{2,3}(?:\.\d)?)" + ("(?:" + lb + ")?" if g.kind == "weight" else lb)))]
        for k, m in wm:
            if not m:
                continue
            v = float(next(x for x in m.groups() if x))
            c, hit[i] = _blank(c, m), True
            if g.kind != "weight":
                ch.notes.append("Weight goals have their own plan. Start one from New plan, then Lose weight.")
                continue
            if k == "now":
                if 80 <= v <= 500 and g.start_weight != v:
                    g.start_weight = v
                    say(f"Weight now: {v:g} lb")
                continue
            if k == "lose":
                if not g.start_weight:
                    ch.notes.append("Add your weight now first, like “I weigh 182”.")
                    continue
                v = g.start_weight - v
            if not 80 <= v <= 500:
                ch.notes.append(f"{v:g} lb doesn't look like a target weight, so it stays as it was.")
            elif g.target_weight != v:
                g.target_weight = v
                say(f"Target weight: {v:g} lb" + (f", {g.start_weight - v:g} lb to lose" if g.start_weight and g.start_weight > v else ""))
            else:
                same = True

        # the date: absolute, "in 10 weeks", or moved by an amount
        noun = "Race" if g.kind == "event" else "Goal date"
        d, m, month_only = _find_date(c, today)
        if not m:
            m = re.search(r"\bin\s+" + _NUM + r"\s+(day|week|month)s?\b", c)
            if m:
                k, unit = _n(m.group(1)), m.group(2)
                d = today + (timedelta(days=k) if unit == "day" else timedelta(weeks=k) if unit == "week" else timedelta(days=30 * k))
                month_only = unit != "day"
        if m:
            c, hit[i] = _blank(c, m), True
            if d and g.kind == "event" and month_only:
                d = d + timedelta(days=6 - d.weekday())       # a month alone: the Sunday of mid-month
            if d is None:
                ch.notes.append("That date doesn't exist, so the date stays as it was.")
            elif d <= today:
                ch.notes.append(f"{_day_say(d, today)} has passed, so the date stays as it was.")
            elif g.target_date != d.isoformat():
                g.target_date = d.isoformat()
                say(f"{noun} moved to {_day_say(d, today)}" if goal.target_date else f"{noun}: {_day_say(d, today)}")
            else:
                same = True
        else:
            m = re.search(r"\b" + _NUM + r"\s+(day|week|month)s?\b", c)
            later = re.search(r"\b(back|later|out|delay|postpone|push)", c)
            sooner = re.search(r"\b(earlier|sooner|forward|ahead|pull|bring)", c)
            if m and (later or sooner):
                c, hit[i] = _blank(c, m), True
                k, unit = _n(m.group(1)), m.group(2)
                delta = timedelta(days=k) if unit == "day" else timedelta(weeks=k) if unit == "week" else timedelta(days=30 * k)
                if not g.target_date:
                    ch.notes.append("There's no date to move yet. Say the date, like “Race is March 1”.")
                else:
                    d = date.fromisoformat(g.target_date) + (-delta if sooner and not later else delta)
                    if d <= today:
                        ch.notes.append(f"That would put it on {_day_say(d, today)}, which has passed. The date stays as it was.")
                    else:
                        g.target_date = d.isoformat()
                        say(f"{noun} moved to {_day_say(d, today)}")
        m = re.search(r"\b(?:no|remove\s+the|drop\s+the)\s+(?:end\s+)?date\b|\bongoing\b|\bopen[\s-]*ended\b", c)
        if m and g.kind != "event":
            c, hit[i] = _blank(c, m), True
            if g.target_date:
                g.target_date = None
                say("No end date")

        # the finish time
        m = re.search(r"\b(?:just\s+(?:want\s+to\s+|to\s+)?finish|finish\s+strong|no\s+time\s+(?:goal|target)|"
                      r"(?:drop|forget|lose)\s+the\s+time(?:\s+goal)?|don'?t\s+care\s+about\s+(?:the\s+)?time)\b", c)
        if m:
            c, hit[i], set_target = _blank(c, m), True, True
            if g.target != "Finish strong":
                g.target = "Finish strong"
                say("Target: finish strong, no time goal")
        secs, sub, m = _finish(c, ev)
        if m:
            c, hit[i] = _blank(c, m), True
            if g.kind != "event":
                ch.notes.append("A finish time needs a race. Say which, like “Make it a 4:20 marathon”.")
            elif ev in DIST_MI:
                lo, hi = _SANE[ev]
                if not lo <= secs <= hi:
                    ch.notes.append(f"{fmt_time(secs)} doesn't fit a {ev}, so the target stays as it was.")
                else:
                    new = ("sub " if sub else "") + fmt_time(secs)
                    set_target = True
                    if g.target != new:
                        g.target = new
                        say(f"Target: {new} ({pace_per_mile(g)} a mile)")
                    else:
                        same = True
            else:
                new = ("sub " if sub else "") + fmt_time(secs)
                set_target = True
                if g.target != new:
                    g.target = new
                    say(f"Target: {new}")

        # the long day
        m = re.search(r"\blong\b[^.]*?" + _WD + r"|" + _WD + r"[^.]*?\blong\b", c)
        if m:
            wd = _WD_IDX[(m.group(1) or m.group(2))[:3]]
            c, hit[i] = _blank(c, m), True
            word = {"run": "Long runs", "bike": "Long rides"}.get(g.sport or EVENT_SPORT.get(ev, ""), "Long sessions")
            if wd in g.no_days:
                g.no_days.remove(wd)
                say(f"{DAY_NAMES[wd]}s are back on")
            if g.long_day != wd:
                g.long_day = wd
                say(f"{word} on {DAY_NAMES[wd]}s")
            else:
                same = True

        # main sport
        m = (re.search(r"\b(running|run|riding|ride|biking|bike|cycling|skiing|ski)\s+instead\b", c)
             or re.search(r"\b(?:switch|change|make\s+it|focus|mostly|main\s+sport|prefer|more)\b[^.]*?\b(running|run|riding|ride|biking|bike|cycling|skiing|ski)\b", c))
        if m:
            sp = _SPORT_W[m.group(1)]
            c, hit[i] = _blank(c, m), True
            if g.sport != sp:
                g.sport = sp
                say(f"Main sport: {_SPORT_SAY[sp]}")
            else:
                same = True

        # days you can't train, and days you can again
        days = [_WD_IDX[x.group(1)[:3]] for x in re.finditer(_WD, c)]
        if re.search(r"\bweekends?\b", c):
            days += [5, 6]
        if re.search(r"\bweekdays?\b", c):
            days += [0, 1, 2, 3, 4]
        anyday = re.search(r"\b(?:any|every)\s*day\b|\ball\s+week\b", c)
        if days or anyday:
            rest = re.sub(_WD + r"|\bweekends?\b|\bweekdays?\b|\b(?:and|or|&|nor)\b|[\s,/]", "", c)
            neg = re.search(r"\b(can'?t|cannot|can\s+not|won'?t|unable|no|not|never|off|unavailable|busy|skip|rest|away|stop|drop|"
                            r"without|except|nothing\s+on)\b", c)
            pos = re.search(r"\b(can|could|able|available|free|open|back|add|again|fine|ok|okay|works?)\b", c)
            pol = "off" if neg else "on" if pos else (last_pol if not rest else None)
            if anyday and pol == "on" and not days:
                hit[i] = True
                if g.no_days:
                    g.no_days = []
                    say("Every day is open again")
                else:
                    same = True
            elif pol and days:
                hit[i], last_pol = True, pol
                days = sorted(set(days))
                if pol == "off":
                    add = [x for x in days if x not in g.no_days]
                    if len(set(g.no_days) | set(add)) >= 7:
                        ch.notes.append("That leaves no days to train, so the days stay as they were.")
                    elif add:
                        g.no_days = sorted(set(g.no_days) | set(add))
                        if g.long_day in add:
                            g.long_day = None
                        say(f"No training on {_days_text(add).replace(' and ', ' or ')}")
                    else:
                        same = True
                else:
                    back = [x for x in days if x in g.no_days]
                    if back:
                        g.no_days = [x for x in g.no_days if x not in back]
                        say(f"{_days_text(back)} are back on")
                    else:
                        same = True
            c = re.sub(_WD, " ", c) if hit[i] else c

        if not hit[i]:
            words = [w for w in re.findall(r"[a-z']+", c) if w not in _FILLER]
            if words:
                ch.missed.append(parts_text(raw, clauses[i]))

    if g.kind == "event" and ev != old_event and old_event and not set_target:
        old = replace(goal)
        if target_seconds(old):
            g.target = ""
            say(f"Target time cleared (it was for the {old_event})")
    ch.understood = bool(ch) or same or bool(ch.notes) or any(hit)
    if ch.missed:
        if ch.understood:
            ch.notes.append("I didn't follow " + " or ".join(f"“{x}”" for x in ch.missed) + ", so I left that alone.")
        else:
            ch.notes.append("I didn't understand that, so nothing changed. " + EXAMPLE)
    elif not ch and same and not ch.notes:
        ch.notes.append("That's already the plan, so nothing changed.")
    return g, ch


def parts_text(raw: str, clause: str) -> str:
    """The clause as you wrote it (original case), trimmed for quoting back."""
    i = raw.lower().find(clause.strip())
    s = raw[i:i + len(clause.strip())] if i >= 0 else clause.strip()
    return (s[:80] + "…") if len(s) > 80 else s


def from_text(text: str, today: date, weight_now: float | None = None) -> tuple[Goal, Changes]:
    """Building from words and changing with words are one path: parse the goal, then apply the
    rest of the sentence ('Run a 4:20 marathon on Feb 1, no Wednesdays')."""
    g = parse_text(text, today, weight_now)
    g, ch = apply_text(g, text, today)
    if g.target == text.strip()[:120]:
        g.target = ""
    return g, ch
