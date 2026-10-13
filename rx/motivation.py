"""Keeping you going: proof of progress from your own numbers, never streaks or badges.

  earned     What yesterday did for you, in one line.
  wins       What's getting better, in plain words (only real changes, against your own past).
  milestones Rare ones that mean something: fitness highest since, resting HR lowest since.
  markers    What we're watching, with where it is and which way it should go.
  calls      How Ceiling's calls held up: when you followed them, did you wake recovered?
"""
from __future__ import annotations

import statistics
from datetime import date, timedelta

from .analysis.progress import UP, DOWN


def _since(series: list[tuple[str, float]], value: float, higher: bool, today: date) -> str | None:
    """'since March' if this is the best value since then (and that's at least 6 weeks back)."""
    for d, v in sorted(series, reverse=True):
        if d >= today.isoformat():
            continue
        if (higher and v > value) or (not higher and v < value):
            dd = date.fromisoformat(d)
            if (today - dd).days >= 42:
                return dd.strftime("%B %Y") if dd.year != today.year else dd.strftime("%B")
            return None
    return "you started recording"


def build(p, rx, store=None) -> dict:
    today = date.fromisoformat(rx.date)
    y = (today - timedelta(days=1)).isoformat()
    out = {"earned": "", "wins": [], "milestones": [], "markers": [], "calls": None}

    # earned
    ld = {x.date: x for x in p.load_days}
    yl = p.daily_load.get(y, 0.0)
    if p.load_days and yl > 10 and len(p.load_days) > 2:
        prev = p.load_days[-2]
        gain = p.load_days[-1].ctl - prev.ctl
        acts = [a for a in p.activities if a.date == y and a.sport != "walk"]
        name = (acts[0].name or acts[0].sport) if acts else "Yesterday"
        out["earned"] = (f"{name} added {gain:.1f} to your fitness. You're at {p.load_days[-1].ctl:.0f}, "
                         f"{'up' if p.load_days[-1].ctl >= (ld.get((today - timedelta(days=29)).isoformat()) or prev).ctl else 'down'} "
                         f"from {((ld.get((today - timedelta(days=29)).isoformat()) or prev).ctl):.0f} a month ago.")
    elif p.load_days:
        w = next((x for x in p.wellness if x.date == rx.date), None)
        out["earned"] = ("Yesterday was a day off. That's when the training turns into fitness"
                         + (f": HRV came in at {w.hrv:.0f} this morning." if w and w.hrv else "."))

    # wins, from the measured trends
    for m in p.progress.measures:
        if m.trend == UP:
            out["wins"].append(m.sentence)
    # milestones
    if p.load_days:
        series = [(x.date, x.ctl) for x in p.load_days]
        now = p.load_days[-1].ctl
        s = _since(series, now, True, today)
        if s and now > 10:
            out["milestones"].append(f"Your fitness ({now:.0f}) is the highest it's been since {s}.")
    rhr = [(w.date, w.rhr) for w in p.wellness if w.rhr]
    if len(rhr) >= 60:
        r14 = statistics.median([v for d, v in rhr[-14:]])
        weekly = [(rhr[i][0], statistics.median([v for _, v in rhr[max(0, i - 13):i + 1]])) for i in range(13, len(rhr) - 14)]
        s = _since(weekly, r14, False, today)
        if s:
            out["milestones"].append(f"Your resting heart rate ({r14:.0f}) is the lowest it's been since {s}.")

    # markers: what we're watching
    hrv = [w.hrv for w in sorted(p.wellness, key=lambda w: w.date) if w.hrv]
    if len(hrv) >= 30:
        a, b = statistics.mean(hrv[-7:]), statistics.mean(hrv[-60:-7])
        out["markers"].append(("HRV, 7-day average", f"{a:.0f} ms", f"Your normal is {b:.0f}. Watching for it to hold "
                               f"or rise as training builds; a week below normal means back off.",
                               "good" if a >= b * 0.95 else "ok"))
    if len(rhr) >= 30:
        a, b = statistics.median([v for _, v in rhr[-7:]]), statistics.median([v for _, v in rhr[-60:-7]])
        out["markers"].append(("Resting heart rate", f"{a:.0f} bpm", f"Your normal is {b:.0f}. Fitter means lower; "
                               f"up 5 or more for days means tired or getting sick.",
                               "good" if a <= b + 1 else "ok"))
    eff = next((m for m in p.progress.measures if m.name.startswith("Aerobic efficiency") and m.now), None)
    if eff:
        out["markers"].append((eff.name, eff.sentence.split(" than")[0].replace("At the same easy heart rate you're", "").strip() or "–",
                               "Speed at the same easy heart rate. The clearest sign your aerobic engine is growing.",
                               "good" if eff.trend == UP else "ok" if eff.trend != DOWN else "bad"))
    prog = getattr(p, "program", None)
    if prog and prog.this_week:
        mon = (today - timedelta(days=today.weekday())).isoformat()
        done = sum((a.moving_s or 0) for a in p.activities if a.date >= mon and a.sport != "walk") / 3600
        out["markers"].append(("This week vs the plan", f"{done:.1f} of {prog.this_week.hours} h",
                               f"{prog.phase} week. {prog.title}.",
                               "good" if done >= prog.this_week.hours * (today.weekday() + 1) / 7 * 0.8 else "ok"))
    return out


def calls(p, store, today: str, days: int = 30) -> dict | None:
    """How the calls held up. For each of the last N days: did you do what the morning said,
    and did you wake up recovered? Then: recovered-rate when you followed vs when you didn't."""
    from .history import day_cell
    import json
    live = {}
    for k in range(1, days + 1):
        d = (date.fromisoformat(today) - timedelta(days=k)).isoformat()
        v = store.get_meta(f"rx:{d}") if store else None
        if v:
            live[d] = tuple(json.loads(v))
    cells = [day_cell(p, (date.fromisoformat(today) - timedelta(days=k)).isoformat(), today, live)
             for k in range(days, 0, -1)]
    cells = [c for c in cells if c.get("status")]
    if len(cells) < 7:
        return None
    followed = [c for c in cells if c["tone"] == "good"]
    not_f = [c for c in cells if c["tone"] in ("bad", "ok")]
    def rate(xs):
        r = [c for c in xs if c.get("rec")]
        return (sum(c["rec"] == "good" for c in r) / len(r) * 100, len(r)) if r else (None, 0)
    rf, nf = rate(followed)
    rn, nn = rate(not_f)
    line = ""
    if rf is not None and rn is not None and nf >= 3 and nn >= 3:
        line = (f"When you did what the morning called for, you woke up recovered {rf:.0f}% of the time. "
                f"When you went over or off-plan, {rn:.0f}%.")
    return {"days": len(cells), "followed": len(followed), "line": line,
            "live": sum(1 for c in cells if not c.get("replayed"))}
