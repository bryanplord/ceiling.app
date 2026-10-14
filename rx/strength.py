"""Strength: programmes, sessions, logging and progression.

The standard is set by the strength apps people love (Moves by Madeline): open it and the day's
workout is just there; a weekly rhythm of lower, upper, full body and conditioning; exercises grouped
in short circuits done for rounds; a full and a short version; every move swaps for the equipment
you have; your weights logged and remembered.

What Ceiling adds, and where those apps are thinnest:
- Progressive overload you can see. Each move is prescribed a weight from your own log (double
  progression: hit the top of the rep range on every set and the weight goes up next time; miss the
  bottom twice and it comes down).
- Fewer moves, done well: 5-7 per session, not 10+.
- Recovery decides the day. A bad morning turns the lift lighter (two rounds, stop three reps short)
  or into mobility; a hard ride or run tomorrow keeps heavy legs off today.
- It fits around endurance: someone who rides or runs lifts two or three times a week, and the
  conditioning day is dropped because the cardio is already there.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta

EQUIP = {"gym": "Full gym", "home": "Dumbbells and a bench", "body": "Bodyweight and bands"}
TRAIN = {"endurance": "Endurance", "strength": "Strength", "both": "Both"}

# id: name per equipment, muscles, cue, pattern, (heavy reps), (light reps), increment in lb (0 = bodyweight)
LIB: dict[str, dict] = {
    "squat": dict(gym="Back squat", home="Goblet squat", body="Tempo squat", muscles="Quads, glutes",
                  cue="Chest up, knees track over toes, sit between your heels.", pat="lower", heavy=(6, 8), light=(10, 12), inc=10),
    "rdl": dict(gym="Romanian deadlift", home="Dumbbell Romanian deadlift", body="Single-leg hip hinge", muscles="Hamstrings, glutes",
                cue="Soft knees, push your hips back, weights close to your legs.", pat="lower", heavy=(6, 8), light=(10, 12), inc=10),
    "lunge": dict(gym="Reverse lunge", home="Dumbbell reverse lunge", body="Reverse lunge", muscles="Quads, glutes",
                  cue="Step back long, front shin vertical, push through the front heel.", pat="lower", heavy=(8, 10), light=(10, 12), inc=5, side=True),
    "thrust": dict(gym="Barbell hip thrust", home="Dumbbell hip thrust", body="Single-leg glute bridge", muscles="Glutes",
                   cue="Ribs down, squeeze at the top for a second.", pat="lower", heavy=(8, 10), light=(12, 15), inc=10),
    "stepup": dict(gym="Dumbbell step-up", home="Dumbbell step-up", body="Step-up", muscles="Quads, glutes",
                   cue="Whole foot on the box, drive up without pushing off the back leg.", pat="lower", heavy=(8, 10), light=(10, 12), inc=5, side=True),
    "calf": dict(gym="Standing calf raise", home="Single-leg calf raise", body="Single-leg calf raise", muscles="Calves",
                 cue="Full range: heel low, pause at the top.", pat="lower", heavy=(10, 12), light=(15, 20), inc=5),
    "bench": dict(gym="Bench press", home="Dumbbell bench press", body="Push-up", muscles="Chest, triceps",
                  cue="Shoulder blades pinched, lower with control, press up and slightly back.", pat="push", heavy=(6, 8), light=(10, 12), inc=5),
    "row": dict(gym="Seated cable row", home="One-arm dumbbell row", body="Band row", muscles="Back, biceps",
                cue="Pull your elbow to your hip, pause, lower slowly.", pat="pull", heavy=(8, 10), light=(10, 12), inc=5),
    "press": dict(gym="Overhead press", home="Dumbbell shoulder press", body="Pike push-up", muscles="Shoulders, triceps",
                  cue="Squeeze your glutes, press straight up, head through at the top.", pat="push", heavy=(6, 8), light=(10, 12), inc=5),
    "pulldown": dict(gym="Lat pulldown", home="Dumbbell pullover", body="Band pulldown", muscles="Lats",
                     cue="Lead with your elbows, chest up to meet the bar.", pat="pull", heavy=(8, 10), light=(10, 12), inc=5),
    "raise": dict(gym="Lateral raise", home="Dumbbell lateral raise", body="Band lateral raise", muscles="Shoulders",
                  cue="Slight bend in the elbows, lift to shoulder height, no swinging.", pat="push", heavy=(10, 12), light=(12, 15), inc=2.5),
    "deadlift": dict(gym="Trap-bar deadlift", home="Dumbbell deadlift", body="Kettlebell or band deadlift", muscles="Glutes, hamstrings, back",
                     cue="Brace, push the floor away, stand tall, lower under control.", pat="lower", heavy=(5, 6), light=(8, 10), inc=10),
    "split": dict(gym="Bulgarian split squat", home="Dumbbell split squat", body="Split squat", muscles="Quads, glutes",
                  cue="Most of your weight on the front leg; back knee drops straight down.", pat="lower", heavy=(8, 10), light=(10, 12), inc=5, side=True),
    "pushup": dict(gym="Push-up", home="Push-up", body="Push-up", muscles="Chest, triceps, core",
                   cue="One straight line from head to heels.", pat="push", heavy=(8, 12), light=(12, 15), inc=0),
    "carry": dict(gym="Farmer carry", home="Farmer carry", body="Suitcase hold", muscles="Grip, core",
                  cue="Tall, shoulders down, walk slowly. 40 m or 40 seconds.", pat="core", heavy=(1, 1), light=(1, 1), inc=5, unit="trip"),
    "deadbug": dict(gym="Dead bug", home="Dead bug", body="Dead bug", muscles="Core",
                    cue="Low back flat on the floor, move slowly, breathe out as you reach.", pat="core", heavy=(8, 10), light=(10, 12), inc=0, side=True),
    "sideplank": dict(gym="Side plank", home="Side plank", body="Side plank", muscles="Obliques",
                      cue="Hips high, straight line from head to feet. 30 seconds a side.", pat="core", heavy=(1, 1), light=(1, 1), inc=0, unit="hold"),
    "pallof": dict(gym="Pallof press", home="Band Pallof press", body="Band Pallof press", muscles="Core",
                   cue="Press straight out and resist the twist.", pat="core", heavy=(10, 12), light=(10, 12), inc=0, side=True),
    "swing": dict(gym="Kettlebell swing", home="Dumbbell swing", body="Jump squat", muscles="Glutes, hamstrings",
                  cue="Snap your hips, arms just guide the weight.", pat="power", heavy=(12, 15), light=(15, 20), inc=5),
    "climber": dict(gym="Mountain climbers", home="Mountain climbers", body="Mountain climbers", muscles="Core, conditioning",
                    cue="Fast feet, hips level. 40 seconds.", pat="power", heavy=(1, 1), light=(1, 1), inc=0, unit="40 s"),
    "rope": dict(gym="Jump rope", home="Jump rope", body="Jumping jacks", muscles="Conditioning",
                 cue="Light, quick contacts. 50 seconds.", pat="power", heavy=(1, 1), light=(1, 1), inc=0, unit="50 s"),
}

# Sessions: circuits of moves, done for rounds. The last circuit drops in the short version.
SESSIONS: dict[str, dict] = {
    "lower": dict(name="Lower body", circuits=[["squat", "deadbug"], ["rdl", "lunge"], ["thrust", "calf"]]),
    "upper": dict(name="Upper body", circuits=[["bench", "row"], ["press", "pulldown"], ["raise", "pallof"]]),
    "full": dict(name="Full body", circuits=[["deadlift", "pushup"], ["split", "row"], ["carry", "sideplank"]]),
    "upper2": dict(name="Upper body", circuits=[["press", "row"], ["pushup", "pulldown"], ["raise", "deadbug"]]),
    "cond": dict(name="Conditioning and core", circuits=[["swing", "climber"], ["stepup", "rope"], ["deadbug", "sideplank"]]),
    "full_ski": dict(name="Legs for the mountains", circuits=[["split", "deadbug"], ["stepup", "rdl"], ["thrust", "sideplank"]]),
    "mobility": dict(name="Mobility", circuits=[]),
}
# The weekly rhythm by days a week. Strength-first follows the Moves pattern (1 lower, 2 upper,
# 1 conditioning, 1 full body at five days); endurance athletes skip conditioning.
RHYTHM = {
    "strength": {2: ["full", "full"], 3: ["lower", "upper", "full"], 4: ["lower", "upper", "full", "cond"],
                 5: ["lower", "upper", "cond", "upper2", "full"]},
    "endurance": {1: ["full"], 2: ["lower", "upper"], 3: ["lower", "upper", "full"]},
}
DAYS = {1: [2], 2: [0, 3], 3: [0, 2, 4], 4: [0, 1, 3, 4], 5: [0, 1, 2, 3, 4]}
MOBILITY = ["Cat-cow, 10 slow reps", "World's greatest stretch, 5 a side", "90/90 hip switches, 10",
            "Couch stretch, 45 seconds a side", "Thoracic rotations, 8 a side", "Calf stretch on a step, 45 seconds a side"]


@dataclass
class Move:
    id: str
    name: str
    muscles: str
    cue: str
    lo: int
    hi: int
    weight: float | None          # prescribed weight (lb or kg), None = choose one / bodyweight
    note: str = ""                # why this weight: "Up 5 lb: you hit 10 on every set last time."
    side: bool = False
    unit: str = "reps"
    bodyweight: bool = False


@dataclass
class Session:
    key: str
    name: str
    rounds: int
    circuits: list[list[Move]]
    minutes: int
    heavy: bool
    mode: str = "full"            # full | short | lighter | mobility
    why: str = ""                 # why today's version (recovery, tomorrow)
    mobility: list[str] = field(default_factory=list)
    failure: bool = False         # last round of each move to failure


# -------------------------------------------------------------------------- settings

def settings(store) -> dict:
    try:
        s = json.loads(store.get_meta("strength_settings") or "{}")
    except ValueError:
        s = {}
    try:
        rounds = max(1, min(8, int(s.get("rounds") or 3)))
    except (TypeError, ValueError):
        rounds = 3
    return {"train": s.get("train") or "endurance", "equip": s.get("equip") or "gym",
            "units": s.get("units") or "lb", "length": s.get("length") or "full",
            "rounds": rounds, "failure": bool(s.get("failure"))}


def save_settings(store, **kw) -> dict:
    s = settings(store)
    s.update({k: v for k, v in kw.items() if v})
    store.set_meta("strength_settings", json.dumps(s))
    return s


# -------------------------------------------------------------------------- logs

def log_for(store, day: str) -> dict | None:
    try:
        v = store.get_meta(f"lift:{day}")
        return json.loads(v) if v else None
    except ValueError:
        return None


def save_log(store, day: str, data: dict) -> None:
    store.set_meta(f"lift:{day}", json.dumps(data))


def all_logs(store) -> dict[str, dict]:
    out = {}
    for r in store.db.execute("SELECT key, value FROM meta WHERE key LIKE 'lift:%'"):
        try:
            out[r["key"][5:]] = json.loads(r["value"])
        except Exception:
            pass
    return dict(sorted(out.items()))


def history(store, ex: str, before: str) -> list[list]:
    """Logged sets for one move, newest session first: [[day, [[weight, reps, done], ...]], ...]."""
    out = []
    for day, lg in sorted(all_logs(store).items(), reverse=True):
        if day >= before:
            continue
        sets = (lg.get("sets") or {}).get(ex)
        if sets and any(s[2] for s in sets):
            out.append([day, [s for s in sets if s[2]]])
    return out


def prescribe_weight(store, ex: str, lo: int, hi: int, inc: float, day: str, units: str) -> tuple[float | None, str]:
    """Double progression from your own log."""
    if not inc:
        return None, ""
    h = history(store, ex, day)
    if not h:
        return None, "First time: pick a weight you could lift two or three more times. Ceiling takes it from there."
    last_day, sets = h[0]
    w = max((s[0] or 0) for s in sets)
    if not w:
        return None, ""
    reps = [s[1] or 0 for s in sets if (s[0] or 0) == w]
    step = inc if units == "lb" else round(inc / 2.2 * 2) / 2
    u = units
    if reps and min(reps) >= hi:
        return w + step, f"Up {step:g} {u}: you hit {hi} on every set last time."
    misses = 0
    for _, ss in h[:2]:
        rr = [s[1] or 0 for s in ss]
        if rr and min(rr) < lo:
            misses += 1
    if misses >= 2:
        nw = round(w * 0.9 / step) * step
        return nw, f"Down to {nw:g} {u}: two sessions under {lo}. Build back up from here."
    return w, f"Same {w:g} {u}: aim for one more rep than last time."


# -------------------------------------------------------------------------- the plan's week

def lift_plan(store, programs, day: date) -> dict | None:
    """The live strength plan's settings: {days, length, rhythm}, or None."""
    for prog in programs or []:
        g = getattr(prog, "goal", prog)
        if getattr(g, "kind", "") == "strength":
            n = int(getattr(g, "lift_days", None) or 3)
            return {"days": max(1, min(5, n)), "id": g.id, "title": getattr(prog, "title", "Get stronger")}
    return None


def _order(kind: str, n: int) -> list[str]:
    return RHYTHM[kind].get(n) or RHYTHM[kind][max(RHYTHM[kind])]


def plan_ahead(store, programs, start: date, days: int = 70, no_days: list[int] | None = None) -> dict:
    """Which lift falls on each day from `start`, the way a coach would run it: you do the next
    session, not the one pinned to a weekday. A lift is due when you still owe lifts this week and
    you've had a day off since the last one (back-to-back is fine at four or more a week, since the
    split alternates). Miss one and it simply moves to the next day you can. What you've already
    logged this week counts. {date: session key}."""
    lp = lift_plan(store, programs, start)
    if not lp:
        return {}
    st = settings(store)
    kind = "strength" if st["train"] == "strength" else "endurance"
    n = lp["days"] if kind == "strength" else min(lp["days"], 3)
    order = _order(kind, n)
    gap = 1 if n >= 4 else 2
    logs = {d: lg for d, lg in all_logs(store).items() if done_sets(lg)[0]}
    mon = start - timedelta(days=start.weekday())
    done_week = sum(1 for d in logs if mon.isoformat() <= d < start.isoformat())
    past = sorted(d for d in logs if d < start.isoformat())
    last = date.fromisoformat(past[-1]) if past else None
    last_key = logs[past[-1]].get("session") if past else None
    out = {}
    for i in range(days):
        d = start + timedelta(days=i)
        if d.weekday() == 0 and i:
            done_week = 0
        if d.weekday() in (no_days or []) or done_week >= n:
            continue
        if last and (d - last).days < gap:
            continue
        k = order[(order.index(last_key) + 1) % len(order)] if last_key in order else order[done_week % len(order)]
        out[d.isoformat()] = k
        done_week += 1
        last, last_key = d, k
    return out


def scheduled(store, programs, day: date, no_days: list[int] | None = None) -> str | None:
    """Which session (key) is due on this day, or None. From today on it follows plan_ahead; a day
    already behind you shows what you logged."""
    from .clock import local_today
    today = local_today()
    if day < today:
        lg = log_for(store, day.isoformat())
        return lg.get("session") if lg and done_sets(lg)[0] else None
    return plan_ahead(store, programs, today, max(14, (day - today).days + 1), no_days).get(day.isoformat())


def rest_reason(store, programs, day: date) -> str:
    """Why there's no lift today, in a sentence."""
    lp = lift_plan(store, programs, day)
    if not lp:
        return ""
    logs = {d for d, lg in all_logs(store).items() if done_sets(lg)[0]}
    y = (day - timedelta(days=1)).isoformat()
    mon = day - timedelta(days=day.weekday())
    wk = sum(1 for d in logs if mon.isoformat() <= d < day.isoformat())
    if y in logs:
        return "You lifted yesterday. Muscle is built on the days between lifts."
    if wk >= lp["days"]:
        return f"That's your {lp['days']} lifts for this week."
    return "A day between lifts."


def session(store, key: str, day: str, verdict: str = "", tomorrow: str = "", recovery_low: bool = False,
            sick: bool = False, length: str | None = None) -> Session:
    st = settings(store)
    eq, units = st["equip"], st["units"]
    length = length or st["length"]
    wk = date.fromisoformat(day).isocalendar()[1]
    heavy = wk % 2 == 0               # alternate heavier and lighter weeks, like the apps people love
    why = ""
    mode = length
    if sick or verdict == "rest":
        return Session(key, "Mobility", 0, [], 20, False, "mobility",
                       "Your body needs the day. Twenty minutes of mobility instead of the lift; the lift moves, it isn't lost.",
                       MOBILITY)
    if key in ("lower", "full", "cond") and tomorrow == "hard":
        key, why = ("upper" if key != "cond" else "full_ski"), "Tomorrow is a hard day, so no heavy legs today: upper body instead."
    if recovery_low or verdict == "light":
        mode = "lighter"
        why = (why + " " if why else "") + "Recovery is down: same moves, two rounds, stop three reps short of hard."
    spec = SESSIONS[key]
    rounds = 2 if mode in ("short", "lighter") else st["rounds"]
    circuits = spec["circuits"][:2] if mode == "short" else spec["circuits"]
    out: list[list[Move]] = []
    for c in circuits:
        row = []
        for ex in c:
            L = LIB[ex]
            lo, hi = L["heavy"] if heavy else L["light"]
            w, note = prescribe_weight(store, ex, lo, hi, L["inc"], day, units)
            if mode == "lighter" and w:
                w = round(w * 0.9 / (5 if units == "lb" else 2)) * (5 if units == "lb" else 2)
                note = f"Lighter today: {w:g} {units}."
            row.append(Move(ex, L[eq], L["muscles"], L["cue"], lo, hi, w, note, L.get("side", False),
                            L.get("unit", "reps"), not L["inc"]))
        out.append(row)
    n_moves = sum(len(c) for c in out)
    minutes = 8 + round(n_moves * rounds * 1.6)        # warm-up plus about 90 seconds a set with rest
    return Session(key, spec["name"], rounds, out, minutes, heavy, mode, why,
                   failure=st["failure"] and mode not in ("lighter",))


def summary(sess: Session) -> str:
    if sess.mode == "mobility":
        return "20 minutes of mobility."
    return (f"{len(sess.circuits)} circuits, {sess.rounds} rounds, about {sess.minutes} minutes"
            + (", heavier week." if sess.heavy else ", lighter week."))


def done_sets(lg: dict | None) -> tuple[int, int]:
    if not lg:
        return 0, 0
    sets = [s for ss in (lg.get("sets") or {}).values() for s in ss]
    return sum(1 for s in sets if s[2]), len(sets)


def lift_load(lg: dict | None) -> float:
    """A rough training load for a lift (so tomorrow knows): about 1 point a hard set."""
    d, _ = done_sets(lg)
    return float(d)


def recent_lifts(store, day: str, days: int = 7) -> list[str]:
    cut = (date.fromisoformat(day) - timedelta(days=days)).isoformat()
    return [d for d, lg in all_logs(store).items() if cut <= d < day and done_sets(lg)[0]]
