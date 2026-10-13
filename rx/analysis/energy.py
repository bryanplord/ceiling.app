"""Energy: how much you have in the tank right now, 0 to 100. Ceiling's own formula.

It charges overnight and drains through the day.

WAKE (what last night put back), out of 100:
  Sleep  40   two-thirds length, one-third quality. Length: hours slept against YOUR need (your
              usual night, 7.5–8.75 h); 60% of need or less scores 0, your full need scores full.
              Quality: Garmin's sleep score, 40 or less scores 0, 90 or more full.
  HRV    35   last night's HRV against your 60-day normal, on a log scale and in units of your own
              night-to-night spread. Blended 70/30 with this week's average, so one good night
              after a bad week doesn't read as fully charged. A normal night scores 28, not 35:
              the top is kept for nights clearly better than usual.
  Resting HR 15   against your normal; each beat above it costs 1.5. Normal scores 13.
  Freshness  10   training load carried in (fitness minus fatigue). Neutral scores 7; deep fatigue 0.

DRAIN, from waking:
  Being awake   2 an hour (a 16-hour day costs about 32)
  Training      a quarter of each session's load (an hour easy is about 12, two hard hours 40+)

The number is now = wake − drain, floored at 0. It is deliberately simple, so you can check it.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta


@dataclass
class Energy:
    wake: int
    now: int
    parts: list = field(default_factory=list)     # (name, points, of, note)
    drain: list = field(default_factory=list)     # (name, points)
    curve: list = field(default_factory=list)     # (hour float, value) wake → now
    say: str = ""


def _clip(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def build(p, day: str, now: datetime | None = None, wake_h: float = 7.0) -> Energy | None:
    ws = sorted(p.wellness, key=lambda w: w.date)
    w = next((x for x in ws if x.date == day), None)
    if not w or not (w.hrv or w.sleep_secs):
        return None
    prior = [x for x in ws if x.date < day][-60:]
    parts = []

    # sleep
    hist = [x.sleep_secs / 3600 for x in prior if x.sleep_secs]
    need = min(8.75, max(7.5, statistics.median(hist))) if len(hist) >= 7 else 8.0
    if w.sleep_secs:
        hrs = w.sleep_secs / 3600
        dur = _clip((hrs / need - 0.6) / 0.4)
        if w.sleep_score:
            qual = _clip((w.sleep_score - 40) / 50)          # score 40 or less = 0, 90+ = full
            sp = 40 * (0.65 * dur + 0.35 * qual)
            q = f", sleep score {w.sleep_score:.0f}"
        else:
            sp, q = 40 * dur, ""
        parts.append(("Sleep", sp, 40, f"{int(hrs)}h {round(hrs % 1 * 60):02d}m against your {need:.1f} h need{q}"))
    else:
        sp = 28
        parts.append(("Sleep", sp, 40, "No sleep data: assumed normal"))

    # HRV
    hv = [x.hrv for x in prior if x.hrv]
    if w.hrv and len(hv) >= 14:
        base = statistics.median(hv)
        logs = [math.log(v) for v in hv]
        sd = max(0.06, statistics.pstdev(logs))
        week = [x.hrv for x in ws if x.hrv and (date.fromisoformat(day) - timedelta(days=6)).isoformat() <= x.date <= day]
        blend = 0.7 * w.hrv + 0.3 * (statistics.mean(week) if week else w.hrv)
        z = math.log(blend / base) / sd
        hp = 35 * _clip(0.8 + 0.2 * z)
        parts.append(("HRV", hp, 35, f"{w.hrv:.0f} last night, {statistics.mean(week):.0f} this week, normal {base:.0f}"))
    else:
        hp = 28
        parts.append(("HRV", hp, 35, "Not enough history: assumed normal"))

    # resting HR
    rh = [x.rhr for x in prior if x.rhr]
    if w.rhr and len(rh) >= 14:
        b = statistics.median(rh)
        rp = 15 * _clip(0.85 - 0.1 * (w.rhr - b))
        parts.append(("Resting HR", rp, 15, f"{w.rhr:.0f} against your normal {b:.0f}"))
    else:
        rp = 13
        parts.append(("Resting HR", rp, 15, "Assumed normal"))

    # freshness carried in
    ld = [x for x in p.load_days if x.date < day]
    tsb = ld[-1].tsb if ld else 0.0
    fp = 10 * _clip(0.7 + tsb / 30)
    parts.append(("Freshness", fp, 10, f"{tsb:+.0f} (fitness minus fatigue)"))

    wake = round(sp + hp + rp + fp)

    # drain
    now = now or datetime.now()
    h_now = now.hour + now.minute / 60 if now.date().isoformat() == day else 22.0
    awake = max(0.0, h_now - wake_h)
    drain = [("Awake", 2 * awake)]
    curve_pts = []
    acts = sorted([a for a in p.activities if a.date == day], key=lambda a: a.start_local or "")
    for a in acts:
        ld_ = p.session_load.get(a.id, 0.0)
        if not ld_:
            continue
        try:
            st = datetime.fromisoformat(a.start_local)
            h0 = st.hour + st.minute / 60
        except (TypeError, ValueError):
            h0 = None
        if h0 is not None and h0 > h_now:
            continue
        cost = 0.25 * ld_
        drain.append((a.name or a.sport.capitalize(), cost))
        if h0 is not None:
            curve_pts.append((h0, (a.moving_s or 0) / 3600, cost))
    total = sum(c for _, c in drain)
    val = max(0, round(wake - total))

    # curve, a point every 15 minutes
    curve = []
    t = wake_h
    while t <= h_now + 1e-6:
        v = wake - 2 * (t - wake_h)
        for h0, dur, cost in curve_pts:
            if t >= h0:
                v -= cost * _clip((t - h0) / max(dur, 0.1))
        curve.append((round(t, 2), max(0.0, v)))
        t += 0.25
    if not curve or curve[-1][0] < h_now:
        curve.append((round(h_now, 2), float(val)))

    say = ("Plenty in the tank." if val >= 70 else "Enough for what today calls for." if val >= 45
           else "Running low. Anything more today should be easy." if val >= 25
           else "Empty. Eat, rest, sleep early.")
    return Energy(wake, val, [(n, round(pt), of, note) for n, pt, of, note in parts],
                  [(n, round(c)) for n, c in drain], curve, say)
