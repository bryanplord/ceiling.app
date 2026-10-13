"""Your cycles, in your own history: where you are vs your best, what was different
then, how to get back, and what you're good and bad at right now.

Data says "HRV 6% above normal". Information says "your normal right now is the
bottom third of your year; in July you were 30% higher, sleeping 45 minutes more
and riding easier. Here's how to get back there."
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np

from .analysis.hr import clean
from .profile import Profile
from .store import Store

WINDOW = 21          # days in a "stretch"
_zone_cache: dict = {}


@dataclass
class Stretch:
    start: str
    end: str
    hrv: float | None = None
    rhr: float | None = None
    sleep_h: float | None = None
    hours_wk: float = 0.0
    days_wk: float = 0.0
    easy: float | None = None
    middle: float | None = None
    hard: float | None = None
    load_day: float = 0.0


@dataclass
class Line:
    text: str
    tone: str = "none"      # good | ok | bad | none
    trend: str = ""         # better | worse | flat | ""


@dataclass
class Story:
    standing: str = ""                  # where your current normal sits in your year
    best: Stretch | None = None
    now: Stretch | None = None
    best_says: str = ""
    differences: list[Line] = field(default_factory=list)
    get_back: list[str] = field(default_factory=list)
    good: list[Line] = field(default_factory=list)
    work_on: list[Line] = field(default_factory=list)
    series: list[tuple[str, float]] = field(default_factory=list)   # 7-day HRV, whole history
    band: tuple[float, float] | None = None


def _hm(h: float) -> str:
    hh, mm = int(h), int(round((h - int(h)) * 60))
    if mm == 60:
        hh, mm = hh + 1, 0
    return f"{hh}h {mm:02d}m"


def _zones(store: Store, aid: str, easy_top: int, hard_bottom: int):
    key = (aid, easy_top, hard_bottom)
    if key not in _zone_cache:
        st = store.stream(aid)
        if not st:
            _zone_cache[key] = None
        else:
            h = clean(st[0], st[1])
            h = h[~np.isnan(h)]
            _zone_cache[key] = (float(np.sum(h <= easy_top)), float(np.sum((h > easy_top) & (h < hard_bottom))),
                                float(np.sum(h >= hard_bottom)))
    return _zone_cache[key]


def _stretch(p: Profile, store: Store, start: date, end: date) -> Stretch:
    s, e = start.isoformat(), end.isoformat()
    days = (end - start).days + 1
    ws = [w for w in p.wellness if s <= w.date <= e]
    acts = [a for a in p.activities if s <= a.date <= e]
    st = Stretch(s, e)
    hv = [w.hrv for w in ws if w.hrv]
    st.hrv = statistics.mean(hv) if len(hv) >= 7 else None
    rh = [w.rhr for w in ws if w.rhr]
    st.rhr = statistics.median(rh) if len(rh) >= 7 else None
    sl = [w.sleep_secs / 3600 for w in ws if w.sleep_secs]
    st.sleep_h = statistics.mean(sl) if len(sl) >= 7 else None
    st.hours_wk = sum(a.moving_s for a in acts) / 3600 / days * 7
    st.days_wk = len({a.date for a in acts}) / days * 7
    st.load_day = sum(v for d, v in p.daily_load.items() if s <= d <= e) / days
    if p.zones:
        et, hb = p.zones.top("Z2 Aerobic"), p.zones.bottom("Z4 Threshold")
        tot = [0.0, 0.0, 0.0]
        for a in acts:
            z = _zones(store, a.id, et, hb)
            if z:
                tot = [x + y for x, y in zip(tot, z)]
        if sum(tot) > 600:
            st.easy, st.middle, st.hard = (x / sum(tot) for x in tot)
    return st


def _rolling(values: dict[str, float], end: date, first: date, win: int) -> list[tuple[str, float]]:
    out, d = [], first + timedelta(days=win - 1)
    while d <= end:
        vs = [values[(d - timedelta(days=i)).isoformat()] for i in range(win)
              if (d - timedelta(days=i)).isoformat() in values]
        if len(vs) >= max(3, win // 2):
            out.append((d.isoformat(), statistics.mean(vs)))
        d += timedelta(days=1)
    return out


def build(store: Store, p: Profile) -> Story:
    story = Story()
    hrv = {w.date: w.hrv for w in p.wellness if w.hrv and w.date < p.as_of}
    if len(hrv) < 45:
        story.standing = (f"{len(hrv)} nights of HRV so far. With 45+ we can show where you are against "
                          f"your own best and worst stretches.")
        return story
    end = date.fromisoformat(p.as_of) - timedelta(days=1)
    first = max(date.fromisoformat(min(hrv)), end - timedelta(days=365))
    story.series = _rolling(hrv, end, first, 7)
    windows = _rolling(hrv, end, first, WINDOW)
    if len(windows) < 10:
        return story
    recent_vals = [hrv[d] for d in sorted(hrv)[-60:]]
    mu = statistics.mean(recent_vals)
    sd = statistics.pstdev(recent_vals)
    story.band = (mu - 0.5 * sd, mu + 0.5 * sd)

    # Where today's normal sits in your year
    now_val = windows[-1][1]
    ranks = sorted(v for _, v in windows)
    pct = 100 * sum(1 for v in ranks if v < now_val) / len(ranks)
    best_d, best_v = max(windows[:-WINDOW] or windows, key=lambda x: x[1])
    worst_v = min(ranks)
    third = "top third" if pct >= 67 else "bottom third" if pct < 33 else "middle"
    story.standing = (f"Your HRV over the last 3 weeks averages {now_val:.0f} ms. That's the {third} of your "
                      f"year: your stretches have ranged from {worst_v:.0f} to {best_v:.0f} ms.")
    if pct < 33:
        story.standing += " So being 'in your normal range' right now isn't the same as being at your best."

    b_end = date.fromisoformat(best_d)
    story.best = _stretch(p, store, b_end - timedelta(days=WINDOW - 1), b_end)
    story.now = _stretch(p, store, end - timedelta(days=WINDOW - 1), end)
    B, N = story.best, story.now
    gap = (B.hrv / N.hrv - 1) * 100 if B.hrv and N.hrv else 0
    fmt = lambda d: date.fromisoformat(d).strftime("%b %-d")
    if gap >= 5:
        story.best_says = (f"Your best stretch was {fmt(B.start)}–{fmt(B.end)}: HRV averaged {B.hrv:.0f} ms, "
                           f"{gap:.0f}% higher than now. Here's what was different.")
    else:
        story.best_says = (f"You're at or near your best stretch of the year right now "
                           f"({B.hrv:.0f} ms, {fmt(B.start)}–{fmt(B.end)}). Keep doing what you're doing.")

    if gap >= 5:
        _differences(story, B, N)
    _strengths(story, p, store)
    return story


def _differences(story: Story, B: Stretch, N: Stretch) -> None:
    D, G = story.differences, story.get_back
    if B.sleep_h and N.sleep_h and abs(B.sleep_h - N.sleep_h) >= 0.25:
        more = B.sleep_h > N.sleep_h
        D.append(Line(f"You slept {_hm(B.sleep_h)} a night then, {_hm(N.sleep_h)} now.", "bad" if more else "good"))
        if more:
            G.append(f"Get {round((B.sleep_h - N.sleep_h) * 60 / 5) * 5} more minutes of sleep a night. "
                     f"Sleep was the biggest difference.")
    if B.easy is not None and N.easy is not None and abs(B.middle - N.middle) >= 0.08:
        D.append(Line(f"{B.middle * 100:.0f}% of your time was in the middle zone then, {N.middle * 100:.0f}% now.",
                      "bad" if N.middle > B.middle else "good"))
        if N.middle > B.middle:
            G.append("Make easy days actually easy. More of your riding has crept into the middle zone, "
                     "which tires you out without building much.")
    if abs(B.hours_wk - N.hours_wk) >= 1.0:
        D.append(Line(f"You trained {B.hours_wk:.1f} hours a week then, {N.hours_wk:.1f} now.", "none"))
        if N.hours_wk > B.hours_wk + 1.5 and N.load_day > B.load_day * 1.15:
            G.append(f"You're doing more now than in your best stretch. Hold volume around "
                     f"{B.hours_wk:.0f} hours a week until HRV comes back up.")
    if abs(B.days_wk - N.days_wk) >= 0.7:
        D.append(Line(f"You trained {B.days_wk:.1f} days a week then, {N.days_wk:.1f} now.", "none"))
        if N.days_wk > B.days_wk:
            G.append(f"Take a real rest day. In your best stretch you had about {7 - B.days_wk:.0f} a week.")
    if B.rhr and N.rhr and abs(B.rhr - N.rhr) >= 2:
        D.append(Line(f"Resting HR was {B.rhr:.0f} then, {N.rhr:.0f} now.", "bad" if N.rhr > B.rhr else "good"))
    if not G:
        G.append("Training looks the same as your best stretch, so the difference is probably outside "
                 "training: stress, travel, alcohol, illness or heat.")


def _strengths(story: Story, p: Profile, store: Store) -> None:
    end = date.fromisoformat(p.as_of) - timedelta(days=1)
    now = _stretch(p, store, end - timedelta(days=27), end)
    then = _stretch(p, store, end - timedelta(days=83), end - timedelta(days=56))
    good, bad = story.good, story.work_on

    def trend(a, b, higher_better=True, tol=0.0):
        if a is None or b is None:
            return ""
        d = (a - b) if higher_better else (b - a)
        return "better" if d > tol else "worse" if d < -tol else "flat"

    # Consistency
    t = trend(now.days_wk, then.days_wk, tol=0.4)
    if now.days_wk >= 5:
        good.append(Line(f"You show up: {now.days_wk:.1f} days a week for the last 4 weeks.", "good", t))
    elif now.days_wk < 3.5:
        bad.append(Line(f"Consistency's slipped: {now.days_wk:.1f} days a week lately.", "bad", t))

    # Volume
    if now.hours_wk >= 7:
        good.append(Line(f"Big aerobic engine work: {now.hours_wk:.1f} hours a week.", "good",
                         trend(now.hours_wk, then.hours_wk, tol=0.7)))

    # Intensity discipline
    if now.easy is not None:
        t = trend(now.middle, then.middle, higher_better=False, tol=0.04) if then.middle is not None else ""
        if now.easy >= 0.75:
            good.append(Line(f"Your easy days are actually easy: {now.easy * 100:.0f}% of your time is easy.", "good", t))
        elif now.middle >= 0.25:
            bad.append(Line(f"Too much 'kind of hard'. {now.middle * 100:.0f}% of your time sits in the middle zone. "
                            f"It feels productive but tires you without making you much faster. Aim for under 20%.",
                            "bad", t))
        if now.hard is not None and now.hard < 0.03:
            bad.append(Line("You haven't really gone hard in 4 weeks. One short, sharp session a week keeps your "
                            "top end.", "ok", ""))

    # Bounce-back
    r = p.response
    if r and r.recovery_days is not None and r.big_days >= 3:
        if r.recovery_days <= 1.5:
            good.append(Line(f"You bounce back fast: after your biggest days, HRV is back to normal in about "
                             f"{max(1, round(r.recovery_days))} day{'s' if round(r.recovery_days) > 1 else ''}.", "good"))
        elif r.recovery_days >= 3:
            bad.append(Line(f"Big days cost you: HRV takes about {round(r.recovery_days)} mornings to come back "
                            f"after your biggest days. Space them out.", "bad"))

    # Sleep
    if now.sleep_h:
        t = trend(now.sleep_h, then.sleep_h, tol=0.15)
        if now.sleep_h >= 7.5:
            good.append(Line(f"You sleep enough: {_hm(now.sleep_h)} a night.", "good", t))
        elif now.sleep_h < 7:
            bad.append(Line(f"Sleep is your limiter: {_hm(now.sleep_h)} a night. For the hours you train, "
                            f"7½–8½ is the target.", "bad", t))

    # Fitness and the heart itself
    for m in p.progress.measures if p.progress else []:
        text = m.sentence if not m.sentence[:1].isdigit() else f"{m.name}: {m.sentence}"
        if m.trend == "improving":
            good.append(Line(text, "good", "better"))
        elif m.trend == "declining":
            bad.append(Line(text, "bad", "worse"))


def history_chart(story: Story, w: int = 1000, h: int = 220) -> str:
    """Your whole year of HRV (7-day average), your best stretch shaded, your normal
    range banded, and today's dot. The point: where you are in your own cycle."""
    pts = story.series
    if len(pts) < 20:
        return ""
    vals = [v for _, v in pts]
    lo, hi = min(vals) * 0.95, max(vals) * 1.04
    d0, d1 = date.fromisoformat(pts[0][0]), date.fromisoformat(pts[-1][0])
    span = max(1, (d1 - d0).days)
    X = lambda d: 40 + (w - 80) * (date.fromisoformat(d) - d0).days / span
    Y = lambda v: h - 30 - (h - 60) * (v - lo) / (hi - lo)
    o = [f'<svg class="hist" viewBox="0 0 {w} {h}" width="100%" role="img" '
         f'aria-label="HRV over the past year with your best stretch highlighted">']
    if story.best:
        x0, x1 = X(story.best.start), X(story.best.end)
        o.append(f'<rect class="bestz" x="{x0:.0f}" y="10" width="{max(4, x1 - x0):.0f}" height="{h - 40}"/>')
        o.append(f'<text class="lbl" x="{(x0 + x1) / 2:.0f}" y="26" text-anchor="middle">best</text>')
    if story.band:
        o.append(f'<rect class="normz" x="40" y="{Y(story.band[1]):.0f}" width="{w - 80}" '
                 f'height="{max(2, Y(story.band[0]) - Y(story.band[1])):.0f}"/>')
    d = " ".join(f"{'M' if i == 0 else 'L'}{X(dd):.1f},{Y(v):.1f}" for i, (dd, v) in enumerate(pts))
    o.append(f'<path class="hline" d="{d}"/>')
    dd, v = pts[-1]
    o.append(f'<circle class="now" cx="{X(dd):.1f}" cy="{Y(v):.1f}" r="6"/>')
    o.append(f'<text class="lbl strong" x="{X(dd) - 12:.0f}" y="{Y(v) - 14:.0f}" text-anchor="end">now {v:.0f}</text>')
    bd, bv = max(pts, key=lambda x: x[1])
    o.append(f'<text class="lbl" x="{X(bd):.0f}" y="{Y(bv) - 10:.0f}" text-anchor="middle">{bv:.0f}</text>')
    for m in range(0, 13):
        md = (d0.replace(day=1) + timedelta(days=32 * m)).replace(day=1)
        if d0 <= md <= d1:
            o.append(f'<text class="tick" x="{X(md.isoformat()):.0f}" y="{h - 6}" text-anchor="middle">'
                     f'{md.strftime("%b")}</text>')
    o.append("</svg>")
    return "".join(o)
