"""Workouts: each session against what that morning prescribed, with a grade.

For every session in the last few weeks: the prescription as it stood that morning, what you
actually did (time in each zone, from the second-by-second heart rate), a grade, the one-line
reason for it, and whether you recovered the next morning.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np

from .analysis.hr import clean
from .prescribe.score import score

ZONES = ["Recovery", "Endurance", "Aerobic", "Tempo", "Threshold", "Max"]
CLS = ["z0", "z1", "z2", "z3", "z4", "z5"]


def bands(p) -> list[tuple[str, str, int, int]]:
    """Display zones (Recovery split off the bottom of zone 1), as (name, class, lo, hi_exclusive)."""
    z = p.zones
    z1top = z.top("Z1 Recovery")
    rec_top = min(round(0.68 * z.lthr), z1top - 6)
    b = [("Recovery", "z0", 0, rec_top + 1), ("Endurance", "z1", rec_top + 1, z1top + 1)]
    names = {"Z2 Aerobic": "Aerobic", "Z3 Tempo": "Tempo", "Z4 Threshold": "Threshold", "Z5 Max": "Max"}
    for i, (n, lo, hi) in enumerate(z.bounds[1:], start=2):
        b.append((names.get(n, n), f"z{i}", lo, 999 if i == len(z.bounds) else hi + 1))
    return b


def zone_minutes(p, t, hr) -> list[float]:
    h = clean(t, hr)
    v = h[~np.isnan(h)]
    if len(v) >= 30:                         # 30-second average, as on the Zones page
        k = np.ones(30) / 30
        v = np.convolve(v, k, mode="valid")
    return [float(np.sum((v >= lo) & (v < hi))) / 60 for _, _, lo, hi in bands(p)]


def grade(s: int) -> tuple[str, str]:
    for cut, g, tone in ((90, "A", "good"), (80, "B", "good"), (70, "C", "ok"), (60, "D", "bad")):
        if s >= cut:
            return g, tone
    return "F", "bad"


def build(store, days: int = 14) -> list[dict]:
    from .service import today_rx, local_today
    end = local_today()
    p_now, _ = today_rx(store)
    w = {x.date: x for x in p_now.wellness}
    hrv = sorted((x.date, x.hrv) for x in p_now.wellness if x.hrv)
    out = []
    for k in range(days, 0, -1):
        d = (end - timedelta(days=k)).isoformat()
        acts = [a for a in p_now.activities if a.date == d and a.sport != "walk" and (a.moving_s or 0) >= 15 * 60]
        if not acts:
            continue
        try:
            p, rx = today_rx(store, d)
        except Exception:
            continue
        nxt = (date.fromisoformat(d) + timedelta(days=1)).isoformat()
        rec = ""
        if nxt in w and w[nxt].hrv:
            base = [v for dd, v in hrv if dd < nxt][-60:]
            if len(base) >= 14:
                u = sum(base) / len(base)
                rec = ("Recovered" if w[nxt].hrv >= u * 0.95 else "Didn't recover" if w[nxt].hrv < u * 0.88
                       else "Mostly recovered") + f": HRV {w[nxt].hrv:.0f} next morning (usual {u:.0f})"
        for a in acts:
            st = store.stream(a.id)
            h, m = divmod(round((a.moving_s or 0) / 60), 60)
            row = {"date": d, "name": a.name or a.sport.capitalize(), "sport": a.sport,
                   "dur": f"{h}h {m:02d}m" if h else f"{m} min",
                   "avg": round(a.avg_hr) if a.avg_hr else None,
                   "rx": (rx.what or rx.do).rstrip("."), "verdict": rx.verdict,
                   "range": f"{rx.hr_low}–{rx.hr_ceiling}" if rx.hr_low else f"under {rx.hr_ceiling}",
                   "rec": rec, "zones": None, "grade": None}
            if st:
                sc = score(rx, st[0], st[1], a.sport)
                g, tone = grade(sc.score)
                mins = zone_minutes(p, st[0], st[1])
                tot = sum(mins) or 1
                row.update(grade=g, tone=tone, score=sc.score, summary=sc.summary, cost=sc.cost,
                           pct=sc.pct_in_range,
                           zones=[(n, c, round(mm), mm / tot * 100) for (n, c, _, _), mm in zip(bands(p), mins)])
            out.append(row)
    return list(reversed(out))


# What a balanced fortnight looks like for someone training 1–3 hours a day: mostly easy,
# a slice of hard (the polarized pattern in Seiler's work on endurance athletes).
TARGET = {"Recovery": 5, "Endurance": 40, "Aerobic": 32, "Tempo": 8, "Threshold": 10, "Max": 5}


def aggregate(rows: list[dict]) -> list[tuple[str, str, int, float, int]]:
    """Total minutes per zone across these workouts, share, and the target share."""
    tot = {n: 0.0 for n in ZONES}
    for r in rows:
        for n, c, mm, _ in r.get("zones") or []:
            tot[n] += mm
    all_ = sum(tot.values()) or 1
    return [(n, CLS[i], round(tot[n]), tot[n] / all_ * 100, TARGET[n]) for i, n in enumerate(ZONES)]
