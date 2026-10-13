"""Training history as a month calendar: for every day, what the morning called for, what you did,
whether that matched, and whether you recovered the next morning.

Days before Ceiling existed have no saved prescription, so the morning's call is REPLAYED: the same
decision Ceiling makes today, run on the data as it stood that morning (sleep, HRV, resting HR and
training load up to the day before). It's what Ceiling would have told you, not what it did.
"""
from __future__ import annotations

import calendar
from datetime import date, timedelta

from .analysis import sleep as sleepbank
from .analysis.reading import cap_verdict, read
from .analysis.recovery import assess
from .prescribe import engine as eng
from .prescribe.engine import EASY, HARD, LIGHT, REST, STEADY

KIND = {REST: "Day off", LIGHT: "Walk", EASY: "Easy", STEADY: "Steady", HARD: "Hard"}
SHORT = {"mountain bike": "MTB", "road ride": "Road", "gravel ride": "Gravel", "trainer ride": "Trainer",
         "run": "Run", "trail run": "Trail run", "ski tour": "Ski tour", "nordic ski": "Nordic",
         "resort skiing": "Ski", "hike": "Hike", "walk": "Walk"}


def replay(p, d: str) -> tuple[str, int | None, int | None]:
    """The verdict (and heart-rate range) Ceiling would have given on the morning of d."""
    wellness = [w for w in p.wellness if w.date <= d]
    daily = {k: v for k, v in p.daily_load.items() if k < d}
    load_days = [ld for ld in p.load_days if ld.date < d]
    rec = assess(wellness, d)
    y = load_days[-1] if load_days else None
    v, _ = eng.decide(eng.Inputs(rec, y, [ld.load for ld in load_days[-2:]]))
    try:
        v = cap_verdict(v, read(wellness, daily, load_days, d, None).cap)
    except Exception:
        pass
    try:
        v = cap_verdict(v, sleepbank.bank(wellness, daily, d).cap)
    except Exception:
        pass
    z = p.zones
    if v == HARD:
        lo, hi = z.bottom("Z4 Threshold"), z.top("Z4 Threshold") + 3
    elif v == STEADY:
        lo, hi = z.bottom("Z2 Aerobic"), z.top("Z2 Aerobic")
    elif v == EASY:
        lo, hi = eng.easy_band(z)
    else:
        lo, hi = None, z.top("Z1 Recovery") - 10
    return v, lo, hi


def _usual_hrv(p, before: str):
    vals = [w.hrv for w in sorted(p.wellness, key=lambda w: w.date) if w.date < before and w.hrv][-60:]
    return sum(vals) / len(vals) if len(vals) >= 14 else None


def day_cell(p, d: str, today: str, live: dict | None = None, store=None) -> dict:
    cell = {"date": d, "day": int(d[-2:]), "future": d > today, "today": d == today}
    if d > today:
        return cell
    extra = None
    if live and d in live:
        v, lo, hi = live[d][:3]
        extra = live[d][3:]
        cell["replayed"] = False
    else:
        v, lo, hi = replay(p, d)
        cell["replayed"] = True
    acts = [a for a in p.activities if a.date == d and (a.moving_s or 0) >= 10 * 60]
    train = [a for a in acts if a.sport != "walk"]
    did = []
    for a in sorted(acts, key=lambda a: -(a.moving_s or 0)):
        k = eng.SUBTYPE.get(a.raw_type or "", a.sport)
        h, m = divmod(round((a.moving_s or 0) / 60), 60)
        did.append(f"{SHORT.get(k, k.capitalize())} {h}h{m:02d}" if h else f"{SHORT.get(k, k.capitalize())} {m}m")
    if d == today and not train:
        return cell                                   # today, nothing done yet: nothing to judge
    main = max(train, key=lambda a: a.moving_s or 0, default=None)
    sc = None
    if main and store is not None:
        # the same score Today and Workouts give, so the three never disagree
        from .review import review, LiteRx
        mm, sp, op = (list(extra) + [None, None, None])[:3] if extra else (None, None, None)
        lrx = LiteRx(d, v, lo, hi, mm, sp, [("", "", "", o[0], o[1]) for o in (op or [])])
        try:
            st = store.stream(main.id)
            rv = review(p, lrx, main, st) if st else None
        except Exception:
            rv = None
        if rv:
            sc = rv.score
    if sc is not None and v not in (REST, LIGHT):
        status = (f"Nailed it, {sc}%", "good") if sc >= 90 else (f"Good, {sc}%", "good") if sc >= 75 else \
                 (f"Close, {sc}%", "ok") if sc >= 60 else (f"Off plan, {sc}%", "bad")
    elif v in (REST, LIGHT):
        status = ("Rested", "good") if not train else ("Trained on a rest day", "bad")
    elif not train:
        status = ("Walked, no workout", "muted") if acts else ("Skipped", "muted")
    elif main and main.avg_hr and hi and main.avg_hr > hi + 3:
        status = ("Too hard", "bad")
    elif v in (STEADY, HARD) and main and main.avg_hr and lo and main.avg_hr < lo - 8:
        status = ("Too easy", "ok")
    else:
        status = ("Nailed it", "good")
    nxt = (date.fromisoformat(d) + timedelta(days=1)).isoformat()
    w = next((x for x in p.wellness if x.date == nxt), None)
    rec = None
    if w and w.hrv and nxt <= today:
        u = _usual_hrv(p, nxt)
        if u:
            rec = "good" if w.hrv >= u * 0.95 else "bad" if w.hrv < u * 0.88 else "ok"
    cell.update(verdict=v, kind=KIND[v], did=did, status=status[0], tone=status[1], rec=rec, score=sc,
                both=status[1] == "good" and rec == "good")
    return cell


def month(p, ym: str, today: str, live: dict | None = None, store=None) -> dict:
    y, m = map(int, ym.split("-"))
    weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(y, m)
    grid = [[(day_cell(p, d.isoformat(), today, live, store) if d.month == m else None) for d in wk] for wk in weeks]
    cells = [c for wk in grid for c in wk if c and not c["future"] and not c["today"]]
    nailed = sum(1 for c in cells if c.get("tone") == "good")
    recov = [c for c in cells if c.get("rec")]
    first = date(y, m, 1)
    prev = (first - timedelta(days=1)).strftime("%Y-%m")
    nxt = (first + timedelta(days=32)).replace(day=1)
    return {"ym": ym, "title": first.strftime("%B %Y"), "grid": grid, "prev": prev,
            "next": nxt.strftime("%Y-%m") if nxt.isoformat() <= today else None,
            "days": len(cells), "nailed": nailed,
            "both": sum(1 for c in cells if c.get("both")),
            "recovered": sum(1 for c in recov if c["rec"] == "good"), "rec_days": len(recov),
            "replayed": any(c.get("replayed") for c in cells)}
