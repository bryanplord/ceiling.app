"""After a workout: how it went against what the morning called for, as a score out of 100,
what you did right, what to fix, and the few numbers worth knowing, each against your own past.

One scorer for every page (Today, Workouts, Calendar), so they never disagree.

The score, out of 100:
  50  Ceiling     time held under the day's ceiling (sustained, so a 15-second spike on a pitch
                  doesn't count like a minute of threshold work)
  30  Range       time in the day's range; on easy days, a bit under it still mostly counts
  20  Duration    close to the planned length: not cut short, not run long
Training on a day off or a walk day caps the score at 50 whatever the numbers say.
"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np

from .analysis.hr import clean, rolling_mean_full
from .sports import BIKE, RUN, SKI, WALK

TERRAIN = {BIKE, SKI}
WORDS = ((90, "Nailed it", "good"), (75, "Good", "good"), (60, "Close", "ok"), (40, "Off plan", "bad"), (0, "Way off", "bad"))
NOUNS = {"mountain bike": ("mountain bike ride", "rides"), "road ride": ("road ride", "road rides"),
         "gravel ride": ("gravel ride", "gravel rides"), "trainer ride": ("trainer ride", "trainer rides"),
         "run": ("run", "runs"), "trail run": ("trail run", "trail runs"), "ski tour": ("ski tour", "tours"),
         "nordic ski": ("nordic ski", "nordic sessions"), "resort skiing": ("ski day", "ski days"),
         "hike": ("hike", "hikes")}


DAY = {"rest": "a day off", "light": "a walk day", "easy": "an easy day", "steady": "a steady day", "hard": "a hard day"}


def word(s: int) -> tuple[str, str]:
    for cut, w, tone in WORDS:
        if s >= cut:
            return w, tone
    return WORDS[-1][1], WORDS[-1][2]


@dataclass
class Review:
    score: int
    word: str
    tone: str
    headline: str
    right: list[str] = field(default_factory=list)
    fix: list[str] = field(default_factory=list)
    stats: list[tuple[str, str, str]] = field(default_factory=list)   # (value, label, note)
    parts: dict = field(default_factory=dict)                          # ceiling/range/duration points
    pct_in_range: int = 0
    minutes_over: int = 0
    peak: int | None = None
    picked: str = ""
    target: int | None = None


def _kind(a) -> str:
    from .prescribe.engine import SUBTYPE
    return SUBTYPE.get(a.raw_type or "", a.sport)


def _mins(title: str) -> int | None:
    m = re.search(r"(\d+) minutes", title or "")
    return int(m.group(1)) if m else None


def _ft(m) -> str:
    return f"{m * 3.281:,.0f} ft"


def _bouts(mask: np.ndarray, min_len: int = 30) -> int:
    """How many separate times the mask was true for at least min_len seconds."""
    n, run = 0, 0
    for x in mask:
        run = run + 1 if x else 0
        if run == min_len:
            n += 1
    return n


def _longest(mask: np.ndarray) -> int:
    best = run = 0
    for x in mask:
        run = run + 1 if x else 0
        best = max(best, run)
    return best


def _rank_note(vals: list[float], v: float, noun_pl: str, higher_word: str, lower_word: str) -> str:
    """Where this one sits among your own: only says something when it's notable."""
    if len(vals) < 5:
        return ""
    below = sum(1 for x in vals if x < v)
    above = sum(1 for x in vals if x > v)
    n = len(vals)
    if above == 0:
        return f"Your {higher_word} of {n + 1} {noun_pl} this year."
    if below == 0:
        return f"Your {lower_word} of {n + 1} {noun_pl} this year."
    share = below / n
    if share >= 0.8:
        return f"{higher_word[:1].upper() + higher_word[1:].replace('highest', 'Higher').replace('longest', 'Longer')} than {share * 100:.0f}% of your {noun_pl}."
    if share <= 0.2:
        return f"{lower_word[:1].upper() + lower_word[1:].replace('lowest', 'Lower').replace('shortest', 'Shorter')} than {(1 - share) * 100:.0f}% of your {noun_pl}."
    return f"About your usual ({statistics.median(vals):.0f})."


def review(p, rx, a, stream, raw: dict | None = None) -> Review | None:
    """rx needs: verdict, hr_low, hr_ceiling, max_minutes, sport, options (may be empty)."""
    from .prescribe.engine import SPORT_OF, EASY, LIGHT, REST, STEADY, HARD, CONTROLLED, easy_band
    if not stream or rx.hr_ceiling is None:
        return None
    h = clean(stream[0], stream[1])
    if np.sum(~np.isnan(h)) < 120:
        return None
    fill = np.where(np.isnan(h), np.nanmean(h), h)
    kind = _kind(a)
    terrain = a.sport in TERRAIN
    sm = rolling_mean_full(fill, 60 if terrain else 30)
    if not len(sm):
        sm = fill
    valid_s = len(sm)
    ceil = rx.hr_ceiling
    lo = rx.hr_low if rx.hr_low is not None else (easy_band(p.zones)[0] if rx.verdict in (EASY,) else 0)
    over = sm > ceil
    inr = (sm >= lo) & (sm <= ceil)
    below = sm < lo
    over_min = int(np.sum(over) / 60)
    in_min = int(np.sum(inr) / 60)
    share_over = np.sum(over) / valid_s
    minutes = round((a.moving_s or len(h)) / 60)
    peak = int(np.nanmax(h))
    walkday = rx.verdict in (REST, LIGHT)

    # what you picked, against the plan and its alternatives
    opts = list(getattr(rx, "options", []) or [])
    chosen = next((o for o in opts if o[4] == kind), None) or \
        next((o for o in opts if SPORT_OF.get(o[4]) == a.sport), None)
    planned = opts[0] if opts else None
    target = _mins(chosen[0]) if chosen else None
    target = target or rx.max_minutes or minutes
    picked = ""
    if chosen and planned and chosen is not planned:
        picked = f"Picked the {chosen[3].split(' ', 1)[-1]} option, judged on its own {target} minutes."
    elif chosen and planned:
        picked = f"The {chosen[3].split(' ', 1)[-1]}, as planned."
    as_planned = a.sport == getattr(rx, "sport", None)
    if as_planned and not chosen:
        picked = ""
    off_menu = bool(opts) and not chosen and not as_planned and not walkday

    # points
    # a 2% grace: one short push on a climb isn't a breach of the day
    ceiling_pts = 50 * max(0.0, 1 - max(0.0, share_over - 0.02) * 4)
    if rx.verdict == HARD:
        range_pts = 30 * min(1.0, in_min / 28)                     # 4 x 8 at threshold, a little slack
    elif rx.verdict == STEADY:
        range_pts = 30 * np.sum(inr) / valid_s
    else:
        range_pts = 30 * (np.sum(inr) + 0.85 * np.sum(below)) / valid_s
    ratio = minutes / max(1, target)
    if ratio < 0.75:
        dur_pts = 20 * max(0.0, ratio / 0.75)
    elif ratio <= 1.1:
        dur_pts = 20.0
    else:
        dur_pts = 20 * max(0.0, 1 - (ratio - 1.1) * 1.5)
    s = ceiling_pts + range_pts + dur_pts
    if off_menu:
        s -= 5
    if walkday and a.sport != WALK:
        s = min(s, 50)
    s = int(round(max(0, min(100, s))))
    w, tone = word(s)

    right, fix = [], []
    bouts = _bouts(over, 30)
    if over_min == 0:
        right.append(f"Never over {ceil}." + (" On these trails, that takes discipline." if kind == "mountain bike" else ""))
    elif share_over < 0.05:
        right.append(f"Only {over_min} min over {ceil}" + (f", in {bouts} short push{'es' if bouts != 1 else ''}." if bouts else "."))
    else:
        where = f", across {bouts} separate climbs or pushes" if terrain and bouts > 1 else ""
        fix.append(f"{over_min} min over {ceil}{where}, peak {peak}. "
                   + ("Walk the steep pitches next time, or pick the road." if terrain and rx.verdict in (EASY, LIGHT)
                      else "Back off as soon as you see the number."))
    if rx.verdict == HARD:
        (right if in_min >= 28 else fix).append(
            f"{in_min} min at {lo}–{ceil}" + (": the work got done." if in_min >= 28 else f", short of the 32 the session called for."))
    elif rx.verdict == STEADY:
        pct = round(100 * np.sum(inr) / valid_s)
        if pct >= 60:
            right.append(f"{pct}% of the time at {lo}–{ceil}.")
        elif np.sum(below) > np.sum(over):
            fix.append(f"Only {pct}% of the time at {lo}–{ceil}: mostly below it, so it trained less than it could have.")
        else:
            fix.append(f"Only {pct}% of the time at {lo}–{ceil}: most of it was above.")
    elif rx.verdict == EASY:
        bl = np.sum(sm < lo - 10) / valid_s
        if bl > 0.5:
            fix.append(f"Over half the time under {lo - 10}: easier than it needed to be. Fine for recovery, light for fitness.")
        else:
            right.append(f"Held it easy: {round(100 * np.sum(sm <= ceil) / valid_s)}% of the time at or under {ceil}.")
    if walkday and a.sport != WALK:
        fix.insert(0, "Today was a walk day. Any real training costs you the recovery it was meant to buy.")
    if ratio < 0.75 and not walkday:
        fix.append(f"{minutes} min of the {target} planned. Short is fine when you're tired; it trains less.")
    elif ratio > 1.1:
        fix.append(f"{minutes} min against {target} planned: {minutes - target} min long.")
    elif not walkday:
        right.append(f"Right length: {minutes} min of the {target} planned.")
    if picked and "option" in picked:
        right.append(picked)
    if off_menu:
        fix.append(f"A {NOUNS.get(kind, (kind, ''))[0]} wasn't one of today's options.")

    # the numbers worth knowing
    stats: list[tuple[str, str, str]] = []
    noun, noun_pl = NOUNS.get(kind, (kind, kind + "s"))
    year_ago = (date.fromisoformat(a.date) - timedelta(days=365)).isoformat()
    same = [x for x in p.activities if x.id != a.id and _kind(x) == kind and year_ago <= x.date and (x.moving_s or 0) >= 15 * 60]
    if a.avg_hr:
        stats.append((f"{a.avg_hr:.0f}", "Average heart rate",
                      _rank_note([x.avg_hr for x in same if x.avg_hr], a.avg_hr, noun_pl, "highest", "lowest")))
    # longest hold in the range: the best measure of control
    hold = _longest(inr) // 60
    if hold >= 5 and rx.verdict != HARD:
        stats.append((f"{hold} min", f"Longest stretch at {lo}–{ceil}",
                      "Unbroken. That's what builds the aerobic engine." if hold >= 20 else "Terrain or effort kept breaking it up."))
    # drift: same effort, does the heart climb? Only meaningful where you control the effort.
    if kind in CONTROLLED and minutes >= 30 and rx.verdict in (EASY, STEADY, LIGHT):
        half = len(fill) // 2
        d1, d2 = float(np.nanmean(h[:half])), float(np.nanmean(h[half:]))
        dr = d2 - d1
        stats.append((f"{dr:+.0f} bpm", "Heart-rate drift, second half",
                      "Low: your aerobic base held for this long." if dr <= 4 else
                      "High: heat, fuel, or this is past what your base covers right now."))
    raw = raw or {}
    elev = a.elev_m or raw.get("total_elevation_gain")
    if elev and a.sport in (BIKE, RUN, SKI) and minutes >= 15:
        per_h = elev / (minutes / 60)
        prev = [x.elev_m / ((x.moving_s or 1) / 3600) for x in same if x.elev_m]
        note = _rank_note(prev, per_h, noun_pl, "steepest", "flattest").replace("(", "(").strip()
        if note.startswith("About your usual"):
            note = f"About your usual climbing rate ({statistics.median(prev) * 3.281:,.0f} ft an hour)." if prev else ""
        stats.append((_ft(elev), f"Climbing, {per_h * 3.281:,.0f} ft an hour", note))
    if a.distance_m and a.moving_s and a.distance_m > 500:
        mi = a.distance_m / 1609.34
        if a.sport == RUN:
            pace = a.moving_s / 60 / mi
            stats.append((f"{int(pace)}:{round(pace % 1 * 60):02d}", f"Pace per mile, {mi:.1f} mi",
                          f"Easy pace is whatever keeps you under {ceil}. It gets faster as you get fitter."
                          if rx.verdict in (EASY, LIGHT) else ""))
        elif a.sport == BIKE:
            mph = mi / (a.moving_s / 3600)
            stats.append((f"{mi:.1f} mi", f"Distance, {mph:.1f} mph average", ""))
    load = p.session_load.get(a.id)
    if load:
        wk = sum(v for d, v in p.daily_load.items()
                 if (date.fromisoformat(a.date) - timedelta(days=6)).isoformat() <= d <= a.date)
        stats.append((f"{load:.0f}", "Training load",
                      f"{load / wk * 100:.0f}% of your last 7 days." if wk > load * 1.05 else "All of your last 7 days."))
    cal = raw.get("calories")
    if cal:
        stats.append((f"{cal:,.0f}", "Calories", "Eat them back today; under-fuelling slows recovery."
                      if rx.verdict in (STEADY, HARD) or cal > 900 else ""))

    # one coach's line
    if s >= 90:
        head = {"easy": "Easy, exactly as asked.", "steady": "Steady and controlled. That's the session.",
                "hard": "The hard work got done, and done right."}.get(rx.verdict, "Done right.")
    elif walkday and a.sport != WALK:
        head = "A training day on a recovery day."
    else:
        # name the one thing that cost the most points, without repeating the list below
        lost = {"over": 50 - ceiling_pts, "range": 30 - range_pts, "length": 20 - dur_pts}
        worst = max(lost, key=lost.get)
        if worst == "over":
            head = f"Harder than {DAY.get(rx.verdict, 'today')} calls for." if rx.verdict != HARD else "The efforts went past threshold."
        elif worst == "range":
            head = ("The efforts never really got to threshold." if rx.verdict == HARD else
                    "Easier than today called for." if np.sum(below) > np.sum(over) else "Off the range most of the way.")
        else:
            head = "Too short to do what today was for." if ratio < 0.75 else "Longer than planned."
        if lost[worst] < 4:
            head = {"easy": "Easy, as asked.", "steady": "Steady, as asked.", "hard": "The work got done."}.get(rx.verdict, "As asked.")
        elif s >= 75:
            head = "Good session. " + head.replace("Harder than", "A bit harder than").replace("Easier than", "A bit easier than")
    return Review(s, w, tone, head.strip(), right[:4], fix[:4], stats[:6],
                  {"Ceiling": round(ceiling_pts), "Range": round(range_pts), "Length": round(dur_pts)},
                  round(100 * np.sum(inr) / valid_s), over_min, peak, picked, target)


class LiteRx:
    """The bits of a prescription a review needs, for days where only (verdict, lo, hi) were kept."""
    def __init__(self, date_, verdict, lo, hi, max_minutes=None, sport=None, options=None):
        self.date, self.verdict, self.hr_low, self.hr_ceiling = date_, verdict, lo, hi
        self.max_minutes, self.sport, self.options = max_minutes, sport, options or []
