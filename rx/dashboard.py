"""The home screen's numbers: where you are, how you're trending, and what matters most.

Everything here is computed from the athlete's own data. Each metric carries a
plain-language state, because a number alone doesn't say whether it's good.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np

from .analysis.hr import clean
from .analysis.recovery import AMBER, GREEN, RED, UNKNOWN
from .prescribe.engine import Rx
from .profile import Profile
from .store import Store


@dataclass
class Metric:
    name: str
    value: str
    state: str           # plain word: "Recovered", "Tired", …
    tone: str            # good | ok | bad | none (drives color)
    detail: str
    series: list[float | None] = field(default_factory=list)
    band: tuple[float, float] | None = None   # your normal range, for the chart
    say: str = ""        # what it means, in one sentence. Information, not data.


@dataclass
class Priority:
    headline: str
    why: str
    this_week: list[str]


@dataclass
class Dashboard:
    recovery: Metric
    load: Metric
    fitness: Metric
    freshness: Metric
    sleep: Metric
    hrv: Metric
    rhr: Metric
    mix: dict[str, float]          # easy / middle / hard share of the last 28 days
    weekly_hours: float
    priority: Priority
    has_data: bool


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def _fmt_hm(hours: float) -> str:
    h = int(hours)
    m = int(round((hours - h) * 60))
    return f"{h}h {m:02d}m"


def _recovery(p: Profile, rx: Rx) -> Metric:
    rec = rx.recovery
    if rec.known == 0:
        return Metric("Recovery", "—", "No data", "none",
                      "No sleep, HRV or resting HR for last night yet.")
    if rec.reds >= 2:
        state, tone = "Not recovered", "bad"
    elif rec.reds == 1 or rec.ambers >= 2:
        state, tone = "Partly recovered", "ok"
    elif rec.ambers == 1:
        state, tone = "Mostly recovered", "ok"
    else:
        state, tone = "Recovered", "good"
    lines = []
    if rec.hrv.status != UNKNOWN and rec.hrv.baseline:
        d = (rec.hrv.today / rec.hrv.baseline - 1) * 100
        lines.append(f"HRV {rec.hrv.today:.0f} ms, {abs(d):.0f}% {'above' if d >= 0 else 'below'} your normal")
    if rec.rhr.status != UNKNOWN and rec.rhr.baseline:
        d = rec.rhr.today - rec.rhr.baseline
        lines.append(f"resting HR {rec.rhr.today:.0f}, {abs(d):.0f} {'above' if d > 0 else 'below'} normal"
                     if abs(d) >= 0.5 else f"resting HR {rec.rhr.today:.0f}, right at normal")
    return Metric("Recovery", state, state, tone, _cap("; ".join(lines)) + "." if lines else "")


def _load_metrics(p: Profile) -> tuple[Metric, Metric, Metric]:
    days = p.load_days[-42:]
    if not days or days[-1].ctl < 1:
        none = Metric("", "—", "Not enough training history", "none", "")
        return (Metric("Load", "—", "No training yet", "none", ""),
                Metric("Fitness", "—", "No training yet", "none", ""),
                Metric("Freshness", "—", "No training yet", "none", ""))
    y = days[-1]
    ratio = (y.atl / y.ctl - 1) * 100
    if ratio > 30:
        lstate, ltone = "Well above normal", "bad"
    elif ratio > 10:
        lstate, ltone = "Above normal", "ok"
    elif ratio < -20:
        lstate, ltone = "Well below normal", "ok"
    else:
        lstate, ltone = "Normal", "good"
    pct = f"{abs(ratio):.0f}%"
    lsay = {"Well above normal": f"This week is {pct} over your usual load. Too much, too fast.",
            "Above normal": f"You're carrying {pct} more load than usual this week.",
            "Well below normal": f"You've trained {pct} less than usual this week.",
            "Normal": "You're training at your usual load."}[lstate]
    load = Metric("Load", f"{y.atl:.0f}", lstate, ltone,
                  f"Last 7 days, {abs(ratio):.0f}% {'above' if ratio >= 0 else 'below'} your 6-week normal.",
                  [d.atl for d in days], say=lsay)

    first = days[0].ctl
    ch = (y.ctl / first - 1) * 100 if first > 1 else 0.0
    if ch > 5:
        fstate, ftone = "Building", "good"
    elif ch < -5:
        fstate, ftone = "Slipping", "bad"
    else:
        fstate, ftone = "Holding", "ok"
    fitness = Metric("Fitness", f"{y.ctl:.0f}", fstate, ftone,
                     f"{'Up' if ch >= 0 else 'Down'} {abs(ch):.0f}% over the last 6 weeks.",
                     [d.ctl for d in days],
                     say={"Building": f"Your fitness is climbing: up {abs(ch):.0f}% in 6 weeks.",
                          "Slipping": f"You're losing fitness: down {abs(ch):.0f}% in 6 weeks.",
                          "Holding": "Your fitness is flat over the last 6 weeks."}[fstate])

    r = y.tsb / y.ctl
    if r > 0.15:
        sstate, stone, ssay = "Fresh", "good", "You're fresh. A hard day would land well."
    elif r > -0.05:
        sstate, stone, ssay = "Neutral", "good", "Not tired, not fresh. Good for steady work."
    elif r > -0.30:
        sstate, stone, ssay = "Training tired", "ok", "You're carrying productive fatigue. Steady work yes, max efforts no."
    else:
        sstate, stone, ssay = "Overreached", "bad", "You're overreached. Back off until this comes back."
    fresh = Metric("Freshness", f"{y.tsb:+.0f}", sstate, stone,
                   "Fitness minus fatigue. Below zero means you're carrying fatigue.",
                   [d.tsb for d in days], say=ssay)
    return load, fitness, fresh


def _series(wellness, attr, days: int, end: date) -> list[float | None]:
    by = {w.date: getattr(w, attr) for w in wellness}
    return [by.get((end - timedelta(days=i)).isoformat()) for i in range(days - 1, -1, -1)]


def _sleep(p: Profile, end: date) -> Metric:
    hrs = [v / 3600 if v else None for v in _series(p.wellness, "sleep_secs", 14, end)]
    known = [h for h in hrs if h]
    if len(known) < 3:
        return Metric("Sleep", "—", "No data", "none", "No sleep data yet.", hrs)
    last7 = [h for h in hrs[-7:] if h]
    prev7 = [h for h in hrs[:7] if h]
    avg = statistics.mean(last7) if last7 else statistics.mean(known)
    detail = f"Averaging {_fmt_hm(avg)} a night this week."
    tone = "good" if avg >= 7.5 else "ok" if avg >= 6.75 else "bad"
    state = "Enough" if tone == "good" else "A little short" if tone == "ok" else "Short"
    if prev7 and last7:
        d = (statistics.mean(last7) - statistics.mean(prev7)) * 60
        if abs(d) >= 10:
            detail += f" {abs(d):.0f} min {'more' if d > 0 else 'less'} than the week before."
    short = max(0, round((7.5 - avg) * 60))
    say = ("You're sleeping enough." if tone == "good" else
           f"You're {short} minutes a night under 7½ hours." if tone == "ok" else
           f"You're short on sleep: {short} minutes a night under 7½ hours. It caps everything else.")
    return Metric("Sleep", _fmt_hm(avg), state, tone, detail, hrs, say=say)


def _vs_normal(p: Profile, attr: str, name: str, unit: str, higher_good: bool, end: date) -> Metric:
    s = _series(p.wellness, attr, 30, end)
    vals = [v for v in s if v]
    if len(vals) < 7:
        return Metric(name, "—", "No data", "none", f"No {name} data yet.", s)
    base = [v for v in s[:-7] if v] or vals
    mu, sd = statistics.mean(base), statistics.pstdev(base) or 1
    recent = statistics.mean([v for v in s[-7:] if v] or vals[-1:])
    z = (recent - mu) / sd * (1 if higher_good else -1)
    tone = "good" if z > 0.3 else "bad" if z < -0.5 else "none"
    diff = abs(recent - mu)
    if name == "HRV":
        say = {"good": "HRV is above your normal. You're absorbing the training.",
               "none": "HRV is in your normal range.",
               "bad": f"HRV is {diff:.0f} ms below your normal. You're not fully absorbing the training."}[tone]
    else:
        say = {"good": "Resting HR is below your normal. A good sign.",
               "none": "Resting HR is in your normal range.",
               "bad": f"Resting HR is up {diff:.0f} bpm on your normal: fatigue, illness, heat or alcohol."}[tone]
    trend = "rising" if recent > mu + 0.3 * sd else "falling" if recent < mu - 0.3 * sd else "steady"
    return Metric(name, f"{vals[-1]:.0f} {unit}", trend.capitalize(), tone,
                  f"Last night {vals[-1]:.0f} {unit}. This week averages {recent:.0f}; your normal is "
                  f"{mu - sd:.0f}–{mu + sd:.0f} (shaded).",
                  s, (mu - sd, mu + sd), say=say)


def _mix(store: Store, p: Profile, end: date) -> tuple[dict[str, float], float]:
    if not p.zones:
        return {}, 0.0
    easy_top = p.zones.top("Z2 Aerobic")
    hard_bottom = p.zones.bottom("Z4 Threshold")
    since = (end - timedelta(days=28)).isoformat()
    secs = {"easy": 0.0, "middle": 0.0, "hard": 0.0}
    total_hours = 0.0
    for a in p.activities:
        if a.date < since or a.date >= end.isoformat():
            continue
        total_hours += a.moving_s / 3600
        st = store.stream(a.id)
        if not st:
            continue
        h = clean(st[0], st[1])
        h = h[~np.isnan(h)]
        secs["easy"] += float(np.sum(h <= easy_top))
        secs["hard"] += float(np.sum(h >= hard_bottom))
        secs["middle"] += float(np.sum((h > easy_top) & (h < hard_bottom)))
    tot = sum(secs.values())
    mix = {k: v / tot for k, v in secs.items()} if tot else {}
    return mix, total_hours / 4


def _priority(p: Profile, rx: Rx, rec: Metric, fitness: Metric, fresh: Metric,
              mix: dict[str, float], weekly: float) -> Priority:
    z = p.zones
    if not z:
        return Priority("Build your profile first.",
                        "Not enough hard efforts recorded yet to set your heart-rate ranges.",
                        ["Ride or run as usual this week; the ranges set themselves as data comes in."])
    e_lo, e_hi = z.bottom("Z2 Aerobic"), z.top("Z2 Aerobic")
    t_lo, t_hi = z.bottom("Z4 Threshold"), z.top("Z4 Threshold")
    wk = f"{weekly:.1f}" if weekly else "0"
    mix_line = (f"Last 4 weeks: {mix['easy']*100:.0f}% easy, {mix['middle']*100:.0f}% middle, "
                f"{mix['hard']*100:.0f}% hard, {wk} hours a week." if mix else "")

    if rec.state == "Not recovered" or fresh.state == "Overreached":
        return Priority("Recovery. Not more training.",
                        f"{rec.detail} {fresh.detail}".strip(),
                        ["Two walk-only days, heart rate under " f"{z.top('Z1 Recovery')}.",
                         "In bed by 9:30 PM until HRV is back to normal.",
                         "No intervals until Ceiling turns you green."])
    if rec.state == "Partly recovered":
        return Priority("Absorb what you've done.",
                        f"{rec.detail} Adding load now buys fatigue, not fitness.",
                        [f"Keep every session under {e_hi} bpm for the next 3 days.",
                         "Hold volume where it is. Don't add hours this week."])
    if fitness.state == "Slipping":
        target = max(weekly * 1.15, weekly + 1)
        return Priority("More training. Volume first.",
                        f"{fitness.detail} {mix_line}".strip(),
                        [f"Build to {target:.1f} hours this week, all easy: HR {e_lo}–{e_hi}.",
                         "No intensity until fitness stops falling."])
    if mix and mix["middle"] > 0.25:
        return Priority("Less middle. Easy easier, hard harder.",
                        f"{mix_line} Too much time between {e_hi} and {t_lo} bpm: tiring, but not where fitness is built.",
                        [f"Easy days: stay under {e_hi} bpm, even on climbs. Walk them if you have to.",
                         f"One hard session: 4 × 8 min at {t_lo}–{t_hi}, 4 min easy between.",
                         "Everything else easy. Aim for 80% of your time under " f"{e_hi}."])
    if mix and mix["hard"] < 0.05 and fresh.state in ("Fresh", "Neutral"):
        return Priority("Add one hard session.",
                        f"{mix_line} You're recovered and fresh, but almost nothing in your mix raises your ceiling.",
                        [f"This week: 4 × 8 min at {t_lo}–{t_hi}, 4 min easy between.",
                         f"Keep the rest easy, under {e_hi}."])
    target = weekly + 1
    return Priority("More easy volume.",
                    f"{mix_line} Recovery and intensity balance look right; more aerobic time is the lever.".strip(),
                    [f"Add an hour this week: {target:.1f} hours total.",
                     f"The extra time all easy, HR {e_lo}–{e_hi}."])


def build(store: Store, p: Profile, rx: Rx) -> Dashboard:
    end = date.fromisoformat(rx.date)
    rec = _recovery(p, rx)
    load, fitness, fresh = _load_metrics(p)
    mix, weekly = _mix(store, p, end)
    return Dashboard(
        recovery=rec, load=load, fitness=fitness, freshness=fresh,
        sleep=_sleep(p, end),
        hrv=_vs_normal(p, "hrv", "HRV", "ms", True, end),
        rhr=_vs_normal(p, "rhr", "Resting HR", "bpm", False, end),
        mix=mix, weekly_hours=weekly,
        priority=_priority(p, rx, rec, fitness, fresh, mix, weekly),
        has_data=bool(p.activities or p.wellness),
    )


# ---- tiny server-rendered charts (SVG, themed by CSS classes) ----------------

def spark(series: list[float | None], w: int = 280, h: int = 56,
          band: tuple[float, float] | None = None, bars: bool = False) -> str:
    pts = [(i, v) for i, v in enumerate(series) if v is not None]
    if len(pts) < 2:
        return ""
    vals = [v for _, v in pts] + (list(band) if band else [])
    lo, hi = min(vals), max(vals)
    if bars:
        lo = 0.0
    if hi - lo < 1e-9:
        hi = lo + 1
    pad = 4
    n = max(len(series) - 1, 1)
    X = lambda i: pad + (w - 2 * pad) * i / n
    Y = lambda v: h - pad - (h - 2 * pad) * (v - lo) / (hi - lo)
    out = [f'<svg class="spark" viewBox="0 0 {w} {h}" width="100%" height="{h}" preserveAspectRatio="none" aria-hidden="true">']
    if band:
        out.append(f'<rect class="band" x="0" y="{Y(band[1]):.1f}" width="{w}" height="{max(1, Y(band[0]) - Y(band[1])):.1f}"/>')
    if bars:
        bw = (w - 2 * pad) / len(series) * 0.62
        base = Y(lo) if lo >= 0 else Y(0)
        for i, v in pts:
            x = pad + (w - 2 * pad) * (i + 0.5) / len(series) - bw / 2
            y = Y(v)
            out.append(f'<rect class="bar" x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{max(1, h - pad - y):.1f}" rx="2"/>')
    else:
        d = " ".join(f"{'M' if k == 0 else 'L'}{X(i):.1f},{Y(v):.1f}" for k, (i, v) in enumerate(pts))
        out.append(f'<path class="line" d="{d}"/>')
        i, v = pts[-1]
        out.append(f'<circle class="end" cx="{X(i):.1f}" cy="{Y(v):.1f}" r="3"/>')
    out.append("</svg>")
    return "".join(out)


def ceiling_band(rx: Rx, p: Profile) -> str:
    """Today's heart-rate target as a band, with the ceiling drawn as the brand line."""
    if rx.hr_ceiling is None or not p.zones:
        return ""
    lo = rx.hr_low if rx.hr_low is not None else None
    c = rx.hr_ceiling
    lo_axis = max(50, (lo if lo is not None else c - 40) - 30)
    hi_axis = int(p.max_bpm) + 2
    w = 1000
    X = lambda v: (v - lo_axis) / (hi_axis - lo_axis) * w
    start = lo if lo is not None else lo_axis
    avg = round((lo + c) / 2) if lo is not None else None
    parts = [f'<svg class="ceil" viewBox="0 0 {w} 112" width="100%" aria-label="Heart-rate target {start} to {c}, never above {c}">',
             f'<rect class="track" x="0" y="50" width="{w}" height="16" rx="8"/>',
             f'<rect class="target" x="{X(start):.0f}" y="50" width="{max(8, X(c) - X(start)):.0f}" height="16" rx="8"/>',
             f'<rect class="limit" x="{X(c) - 3:.0f}" y="24" width="6" height="68" rx="3"/>',
             f'<text class="ceilnum" x="{X(c) + 14:.0f}" y="40">Never above {c}</text>']
    if lo is not None:
        parts.append(f'<text class="num" x="{X(lo):.0f}" y="96" text-anchor="start">{lo}</text>')
    if avg is not None:
        parts.append(f'<rect class="avg" x="{X(avg) - 2:.0f}" y="46" width="4" height="24" rx="2"/>')
    parts.append(f'<text class="num muted" x="{w}" y="96" text-anchor="end">Max {int(p.max_bpm)}</text>')
    parts.append("</svg>")
    return "".join(parts)


ZONE_SHORT = {"Z1 Recovery": "Recovery", "Z2 Aerobic": "Aerobic", "Z3 Tempo": "Tempo",
              "Z4 Threshold": "Threshold", "Z5 Max": "Max"}


def room(rx: Rx, p: Profile) -> str:
    """Today's heart rate as a room: your zones as floors, today's band in ink,
    and the ceiling drawn as the brand's orange line. Vertical, because a ceiling is above you."""
    if rx.hr_ceiling is None or not p.zones:
        return ""
    c = rx.hr_ceiling
    lo = rx.hr_low
    top = int(p.max_bpm) + 4
    floor = max(int(p.rest_hr) + 10, (lo if lo is not None else c - 30) - 22)
    floor -= floor % 10
    W, H, T, B = 400, 520, 34, 500          # T/B: y of top and bottom of the scale
    X0, X1 = 58, 252                         # the column
    Y = lambda v: B - (B - T) * (v - floor) / (top - floor)
    o = [f'<svg class="room" viewBox="0 0 {W} {H}" role="img" '
         f'aria-label="Heart rate {lo if lo is not None else floor} to {c}. Never above {c}. Your max is {int(p.max_bpm)}.">']
    # zones as floors
    for i, (name, zlo, zhi) in enumerate(p.zones.bounds):
        a, b = max(zlo, floor), min(zhi + 1, top)
        if b <= a:
            continue
        o.append(f'<rect class="z{i % 2}" x="{X0}" y="{Y(b):.1f}" width="{X1 - X0}" height="{Y(a) - Y(b):.1f}"/>')
        o.append(f'<text class="zl" x="{X1 - 10}" y="{(Y(a) + Y(b)) / 2 + 5:.1f}" text-anchor="end">{ZONE_SHORT.get(name, name)}</text>')
    # bpm ticks
    for v in range(floor, top + 1, 10):
        o.append(f'<text class="tick" x="{X0 - 12}" y="{Y(v) + 5:.1f}" text-anchor="end">{v}</text>')
    # max
    mx = int(p.max_bpm)
    o.append(f'<line class="maxl" x1="{X0}" x2="{W}" y1="{Y(mx):.1f}" y2="{Y(mx):.1f}"/>')
    o.append(f'<text class="side" x="{X1 + 14}" y="{Y(mx) - 8:.1f}">your max {mx}</text>')
    # today's band
    if lo is not None:
        o.append(f'<rect class="band" x="{X0}" y="{Y(c):.1f}" width="{X1 - X0}" height="{Y(lo) - Y(c):.1f}"/>')
        avg = round((lo + c) / 2)
        o.append(f'<rect class="avg" x="{X0}" y="{Y(avg) - 1.5:.1f}" width="{X1 - X0}" height="3"/>')
        o.append(f'<text class="side strong" x="{X1 + 14}" y="{Y(avg) + 6:.1f}">avg {avg}</text>')
        o.append(f'<text class="side" x="{X1 + 14}" y="{Y(lo) + 6:.1f}">{lo}</text>')
        o.append(f'<line class="lol" x1="{X1}" x2="{X1 + 8}" y1="{Y(lo):.1f}" y2="{Y(lo):.1f}"/>')
    # the ceiling
    o.append(f'<rect class="ceil" x="{X0 - 6}" y="{Y(c) - 3.5:.1f}" width="{W - X0 + 6}" height="7" rx="3.5"/>')
    o.append(f'<text class="cnum" x="{W}" y="{Y(c) - 16:.1f}" text-anchor="end">{c}</text>')
    o.append("</svg>")
    return "".join(o)
