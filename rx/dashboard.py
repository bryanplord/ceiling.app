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
    # The tile is about LAST NIGHT, and about quality as much as length: 8½ hours of broken
    # sleep is not a good night. Quality = Garmin's sleep score, cross-checked against what
    # your overnight HRV did (a restful night shows up as normal-or-better HRV).
    ws = sorted(p.wellness, key=lambda w: w.date)
    last = next((w for w in reversed(ws) if w.date <= end.isoformat() and w.sleep_secs), None)
    night = last.sleep_secs / 3600 if last else avg
    hv = [w.hrv for w in ws if w.hrv and last and w.date < last.date][-60:]
    hrv_ok = None
    if last and last.hrv and len(hv) >= 14:
        hrv_ok = last.hrv >= statistics.median(hv) * 0.93
    sc = last.sleep_score if last else None
    if sc:
        q = "good" if sc >= 80 and hrv_ok is not False else "ok" if sc >= 60 else "bad"
    elif hrv_ok is not None:
        q = "good" if hrv_ok else "ok"
    else:
        q = "none"
    qword = {"good": "Good quality", "ok": "Fair quality", "bad": "Poor quality", "none": "Quality unknown"}[q]
    length = "good" if night >= 7.5 else "ok" if night >= 6.75 else "bad"
    tone = "bad" if "bad" in (q, length) else "ok" if "ok" in (q, length) else "good"
    state = qword + (f", score {sc:.0f}" if sc else "")
    if length != "good":
        state = ("Short, " if length == "bad" else "A little short, ") + qword.lower()
    detail = f"Last night {_fmt_hm(night)}." + (f" Sleep score {sc:.0f}." if sc else "") + " " + detail
    say = {("good", "good"): "A good night: long enough and restful.",
           ("good", "ok"): "Long enough, but not your most restful night.",
           ("good", "bad"): "Long enough in bed, but poor quality: count it as a shorter night.",
           ("good", "none"): "Long enough. Quality unknown."}.get((length, q)) or (
           f"Short: {_fmt_hm(night)}, against the 7½ hours that holds everything else up.")
    if hrv_ok is False and sc and sc >= 80:
        say += " Your HRV disagrees with the score, though: your body didn't recover as well as it looks."
    return Metric("Sleep", _fmt_hm(night), state, tone, detail, hrs, say=say)



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


from .analysis.hr import ZONE_LABEL as ZONE_SHORT
ZONE_FEEL = {"Z1 Recovery": "barely working", "Z2 Aerobic": "all day, full sentences",
             "Z3 Tempo": "comfortably hard, short sentences", "Z4 Threshold": "hard, a few words",
             "Z5 Max": "all out, no words"}


def _marks(mark, Y, lo, c, AX, L0, L1, SC, R0, R1, W, T, B, bands) -> str:
    """Alternative ways to show today's range (design exploration)."""
    yt, yb = Y(c + 1), Y(lo)
    o = []
    if mark == "spotlight":       # dim everything outside the range
        for y1, y2 in ((T - 30, yt), (yb, B + 4)):
            o.append(f'<rect fill="#fff" opacity=".62" x="{L0}" y="{y1:.1f}" width="{R1 - L0}" height="{y2 - y1:.1f}"/>')
    elif mark == "brackets":      # ink brackets on the scale
        x = AX + 6
        o.append(f'<path d="M{x + 8},{yt:.1f} H{x} V{yb:.1f} H{x + 8}" fill="none" stroke="#0a0a0a" stroke-width="2.5"/>')
        o.append(f'<path d="M{L1 + 4},{yt:.1f} H{L1 + 12} V{yb:.1f} H{L1 + 4}" fill="none" stroke="#0a0a0a" stroke-width="2.5"/>')
    elif mark == "hairlines":     # two dashed lines across the chart at the edges
        for y, v in ((yt, c), (yb, lo)):
            o.append(f'<line x1="{L0}" x2="{R1}" y1="{y:.1f}" y2="{y:.1f}" stroke="#0a0a0a" stroke-width="1.2" stroke-dasharray="4 3"/>')
    elif mark == "spine":         # the scale itself goes thick and black over the range
        o.append(f'<rect x="{L0 - 6}" y="{yt:.1f}" width="6" height="{yb - yt:.1f}" fill="#0a0a0a"/>')
    elif mark == "pointer":       # a triangle at the aim, pointing into your zones
        ya = Y((lo + c) / 2)
        o.append(f'<path d="M{L0 - 14},{ya - 8:.1f} L{L0 - 2},{ya:.1f} L{L0 - 14},{ya + 8:.1f}Z" fill="#0a0a0a"/>')
        for y in (yt, yb):
            o.append(f'<line x1="{L0 - 10}" x2="{L0}" y1="{y:.1f}" y2="{y:.1f}" stroke="#0a0a0a" stroke-width="2"/>')
    elif mark == "window":        # an inset frame inside the zones panel
        o.append(f'<rect x="{L0 + 4}" y="{yt + 2:.1f}" width="{L1 - L0 - 8}" height="{yb - yt - 4:.1f}" fill="none" stroke="#fff" stroke-width="3"/>')
        o.append(f'<rect x="{L0 + 1}" y="{yt:.1f}" width="{L1 - L0 - 2}" height="{yb - yt:.1f}" fill="none" stroke="#0a0a0a" stroke-width="1.5"/>')
    elif mark == "hatch":         # diagonal stripes across the zones panel
        o.append('<defs><pattern id="hx" width="8" height="8" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
                 '<line x1="0" y1="0" x2="0" y2="8" stroke="#0a0a0a" stroke-width="1.2" opacity=".35"/></pattern></defs>')
        o.append(f'<rect x="{L0}" y="{yt:.1f}" width="{L1 - L0}" height="{yb - yt:.1f}" fill="url(#hx)"/>')
    elif mark == "tab":           # a tab sticking out of the zones panel
        o.append(f'<rect x="{L1}" y="{yt:.1f}" width="34" height="{yb - yt:.1f}" fill="#0a0a0a"/>')
        o.append(f'<text x="{L1 + 17}" y="{(yt + yb) / 2:.1f}" fill="#fff" font-size="12" font-weight="700" text-anchor="middle" '
                 f'transform="rotate(-90 {L1 + 17} {(yt + yb) / 2:.1f})" dy="4">TODAY</text>')
    elif mark == "glow":          # a soft band, no hard edges
        o.append('<defs><linearGradient id="gl" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#0a0a0a" stop-opacity="0"/>'
                 '<stop offset=".5" stop-color="#0a0a0a" stop-opacity=".12"/><stop offset="1" stop-color="#0a0a0a" stop-opacity="0"/></linearGradient></defs>')
        pad = (yb - yt) * 0.35
        o.append(f'<rect x="{L0}" y="{yt - pad:.1f}" width="{R1 - L0}" height="{yb - yt + 2 * pad:.1f}" fill="url(#gl)"/>')
    elif mark == "solid":         # the range in full-strength zone color inside the zones panel
        for name, cls, za, zb in bands:
            a_, b_ = max(lo, za), min(c + 1, zb)
            if b_ > a_:
                o.append(f'<rect class="thumb {cls}" style="opacity:.75" x="{L0}" y="{Y(b_):.1f}" width="{L1 - L0}" height="{Y(a_) - Y(b_):.1f}"/>')
    for y, v in ((yt, c), (yb, lo)):
        o.append(f'<rect fill="#fff" x="0" y="{y - 9:.1f}" width="{AX + 1}" height="18"/>')
        o.append(f'<text class="bnum" x="{AX}" y="{y + 4.5:.1f}" text-anchor="end">{v}</text>')
    return "".join(o)


def room(rx: Rx, p: Profile, hist=None, usual: float | None = None, mode: str = "stretch", mark: str = "hairlines",
         hist_label: str = "where you trained, last 4 weeks") -> str:
    """Your heart today, built around a center spine of heart-rate numbers.
    Left: your zones, named and colored, reading out from the spine.
    Right: where you actually trained the last 4 weeks, one bar per 2 beats, colored by zone.
    Today's range: a black bar on the spine."""
    if rx.hr_ceiling is None or not p.zones:
        return ""
    c = rx.hr_ceiling
    lo = rx.hr_low
    rest = int(round(p.rest_hr))
    mx = int(p.max_bpm)
    band_lo = lo if lo is not None else c - 25
    top = mx + 4
    floor = rest - 3
    knee = max(floor + 10, min(band_lo, p.zones.bottom("Z2 Aerobic")) - 20)
    W, H, T, B = 640, 780, 24, 752
    KY = B - (B - T) * 0.09
    AX = 40                         # heart-rate numbers, right-aligned here
    L0, L1 = 52, 296                # zones panel
    SC = 330                        # the day's slider
    R0, R1 = 368, 640               # where-you-trained panel
    stretch = max(1.0, min(2.6, 26 / max(1, c - band_lo)))   # a narrow range gets room; a wide one doesn't squeeze the rest
    segs = [(knee, band_lo, 1.0), (band_lo, c, stretch), (c, top, 1.0)]
    units = sum((bb - aa) * w for aa, bb, w in segs if bb > aa)

    zb_ = [(floor if i == 0 else zlo, (top if i == len(p.zones.bounds) - 1 else zhi + 1))
           for i, (_, zlo, zhi) in enumerate(p.zones.bounds)]

    def Y(v):
        if mode == "linear":
            return B - (B - T) * (v - floor) / (top - floor)
        if mode == "equal":
            n = len(zb_)
            for i, (a_, b_) in enumerate(zb_):
                if v <= b_ or i == n - 1:
                    f = (min(max(v, a_), b_) - a_) / max(1, b_ - a_)
                    return B - (B - T) * (i + f) / n
        if v <= knee:
            return B - (B - KY) * (v - floor) / max(1, knee - floor)
        u = 0.0
        for aa, bb, w in segs:
            if bb > aa:
                u += (min(max(v, aa), bb) - aa) * w
        return KY - (KY - T) * u / units

    def zone_of(v):
        for i, (_, zlo, zhi) in enumerate(p.zones.bounds):
            if (i == 0 and v <= zhi) or zlo <= v <= zhi or (i == len(p.zones.bounds) - 1 and v >= zlo):
                return i + 1
        return 1

    yt, yb = Y(c + 1), Y(band_lo)
    # Display bands: Recovery is split off the bottom of zone 1 (under ~68% of threshold, Coggan's
    # active recovery); the rest of zone 1, up to the aerobic band, is Endurance.
    z1top = p.zones.top("Z1 Recovery")
    rec_top = min(round(0.68 * p.zones.lthr), z1top - 6)
    bands = [("Recovery", "z0", floor, rec_top + 1), ("Endurance", "z1", rec_top + 1, z1top + 1)]
    for i, (name, zlo, zhi) in enumerate(p.zones.bounds[1:], start=2):
        bands.append((ZONE_SHORT.get(name, name), f"z{i}", zlo, top if i == len(p.zones.bounds) else zhi + 1))
    o = [f'<svg class="room" viewBox="0 0 {W} {H}" role="img" '
         f'aria-label="Your heart today: {band_lo} to {c}. Resting {rest}, max {mx}.">']

    # ZONES, colored and named
    bounds = []
    for k_, (name, cls, za, zb) in enumerate(bands):
        za, zb = max(za, floor), min(zb, mx)
        if zb <= za:
            continue
        y1, y2 = Y(zb), Y(za)
        o.append(f'<rect class="zf {cls}" x="{L0}" y="{y1:.1f}" width="{L1 - L0}" height="{y2 - y1:.1f}"/>')
        if y2 - y1 >= 16:
            o.append(f'<text class="zn" x="{L0 + 14}" y="{(y1 + y2) / 2 + 5:.1f}">{name}</text>')
        if k_ > 0:
            bounds.append(za)

    # NUMBERS on the left: every zone edge, resting and max, and round tens where there's room
    edges = [rest, *bounds, mx]
    nums = list(edges)
    for v in range((knee // 10 + 1) * 10, top, 10):
        if all(abs(Y(v) - Y(e)) >= 24 for e in nums):
            nums.append(v)
    for v in range((knee // 5 + 1) * 5, top, 5):
        if v not in nums:
            o.append(f'<line class="hash minor" x1="{L0 - 5}" x2="{L0}" y1="{Y(v):.1f}" y2="{Y(v):.1f}"/>')
    for v in sorted(nums):
        cls = "tick strong" if v in bounds else "tick"
        o.append(f'<text class="{cls}" x="{AX}" y="{Y(v) + 4.5:.1f}" text-anchor="end">{v}</text>')
        o.append(f'<line class="hash{" major" if v in bounds else ""}" x1="{AX + 3}" x2="{L0}" y1="{Y(v):.1f}" y2="{Y(v):.1f}"/>')
    if mode == "stretch":
        o.append(f'<path class="brk" d="M{AX - 20},{KY + 3:.1f} l5,-5 l5,5 l5,-5 l5,5"/>')
    o.append(f'<line class="ref" x1="{L0}" x2="{W}" y1="{Y(mx):.1f}" y2="{Y(mx):.1f}"/>')
    o.append(f'<text class="reft" x="{W}" y="{Y(mx) - 6:.1f}" text-anchor="end">your max</text>')
    o.append(f'<line class="ref" x1="{L0}" x2="{W}" y1="{Y(rest):.1f}" y2="{Y(rest):.1f}"/>')
    o.append(f'<text class="reft" x="{W}" y="{Y(rest) - 6:.1f}" text-anchor="end">resting</text>')

    # TODAY: a box over the range, one segment per band it crosses, in that band's color
    if mark == "box":
        o.append(f'<rect fill="#fff" x="{SC - 24}" y="{yt:.1f}" width="48" height="{yb - yt:.1f}"/>')
        for name, cls, za, zb in bands:
            a_, b_ = max(band_lo, za), min(c + 1, zb)
            if b_ > a_:
                o.append(f'<rect class="thumb {cls}" x="{SC - 24}" y="{Y(b_):.1f}" width="48" height="{Y(a_) - Y(b_):.1f}"/>')
        o.append(f'<text class="bnum" x="{SC}" y="{yt + 15:.1f}" text-anchor="middle">{c}</text>')
        if lo is not None:
            o.append(f'<text class="bnum" x="{SC}" y="{yb - 6:.1f}" text-anchor="middle">{band_lo}</text>')

    # RIGHT: where you trained, a histogram in 2-beat bins, each bin in its band's color
    if hist is not None and hist.sum() > 0:
        v0, v1 = max(floor, knee) // 2 * 2, min(mx + 1, len(hist))
        bins = [(v, float(hist[v:v + 2].sum())) for v in range(v0, v1, 2)]
        peak = max(x for _, x in bins) or 1.0
        span = (R1 - R0) * 0.5

        def band_of(v):
            return next((cls for _, cls, za, zb in bands if za <= v < zb), "z1")
        pts = []
        for v, x in bins:
            w_ = span * x / peak
            y1, y2 = Y(v + 2), Y(v)
            if w_ > 0.3:
                o.append(f'<rect class="hc {band_of(v)}" x="{R0}" y="{y1:.1f}" width="{w_:.1f}" height="{y2 - y1:.1f}"/>')
            pts += [(R0 + w_, y2), (R0 + w_, y1)]
        o.append('<path class="hl" d="' + " ".join(f"{'M' if j == 0 else 'L'}{x:.1f},{y:.1f}" for j, (x, y) in enumerate(pts)) + '"/>')
        o.append(f'<text class="small" x="{R0}" y="{B + 18}">{hist_label}</text>')
        o.append(f'<text class="small" x="{L0}" y="{B + 18}">your zones</text>')
        if mark == "box":
            o.append(f'<text class="small" x="{SC}" y="{B + 18}" text-anchor="middle">today</text>')


    if mark != "box":
        o.append(_marks(mark, Y, band_lo, c, AX, L0, L1, SC, R0, R1, W, T, B, bands))

    # today's target: the number that matters, big, at its height; the range under it, small
    aim = round((band_lo + c) / 2) if lo is not None else c
    ya = Y(aim)
    o.append(f'<text class="tlab" x="{W - 4}" y="{ya - 30:.1f}" text-anchor="end">{"AIM" if lo is not None else "UNDER"}</text>')
    o.append(f'<text class="taimbig" x="{W - 4}" y="{ya + 14:.1f}" text-anchor="end">{aim}</text>')
    if lo is not None:
        o.append(f'<text class="trng2" x="{W - 4}" y="{ya + 38:.1f}" text-anchor="end">stay {band_lo}–{c}</text>')
    o.append("</svg>")
    return "".join(o)
