"""Trends: every signal on one shared timeline, each with what its direction means.

Rows share the same days, so cause and effect line up vertically: a big week in
load, then HRV sagging and resting HR creeping up two days later, sleep debt
building underneath. Each row says, in plain words, which way it's heading and
what that means for you. The top line reads them together (HRV4Training's trend
states: coping well / accumulating fatigue / maladaptation / saturation).
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta

from .analysis import sleep as sleepmod
from .analysis.response import _z_by_date

DAYS = 98


@dataclass
class Row:
    key: str
    name: str
    now: str
    says: str
    tone: str
    svg: str = ""


@dataclass
class Trends:
    headline: str
    tone: str
    why: str
    rows: list[Row] = field(default_factory=list)
    days: list[str] = field(default_factory=list)


def _avg(vals):
    v = [x for x in vals if x is not None]
    return statistics.mean(v) if v else None


def _roll(series: list, n: int = 7, min_n: int = 3) -> list:
    out = []
    for i in range(len(series)):
        win = [x for x in series[max(0, i - n + 1):i + 1] if x is not None]
        out.append(statistics.mean(win) if len(win) >= min_n else None)
    return out


def _band(series: list, n: int = 60) -> list:
    out = []
    for i in range(len(series)):
        win = [x for x in series[max(0, i - n):i] if x is not None]
        if len(win) >= 14:
            mu, sd = statistics.mean(win), statistics.pstdev(win)
            out.append((mu - 0.5 * sd, mu + 0.5 * sd))
        else:
            out.append(None)
    return out


def _pct(a, b):
    return (a / b - 1) * 100 if a is not None and b else None


# --------------------------------------------------------------------------- charts
W, LP, RP = 1000, 6, 46


def _x(i, n):
    return LP + (W - LP - RP) * (i + 0.5) / n


def _frame(h, n, days, cls, label):
    o = [f'<svg class="tr {cls}" viewBox="0 0 {W} {h}" width="100%" preserveAspectRatio="none" role="img" aria-label="{label}">']
    for i, d in enumerate(days):
        if date.fromisoformat(d).weekday() == 0:
            o.append(f'<line class="wk" x1="{_x(i, n) - (W - LP - RP) / n / 2:.1f}" x2="{_x(i, n) - (W - LP - RP) / n / 2:.1f}" y1="0" y2="{h}"/>')
    return o


def _scale(vals, pad=0.08, lo=None, hi=None):
    v = [x for x in vals if x is not None]
    a = min(v) if lo is None else lo
    b = max(v) if hi is None else hi
    if b - a < 1e-6:
        b = a + 1
    r = b - a
    return a - r * pad, b + r * pad


def _path(series, n, Y):
    d, pen = [], False
    for i, v in enumerate(series):
        if v is None:
            pen = False
            continue
        d.append(f"{'L' if pen else 'M'}{_x(i, n):.1f},{Y(v):.1f}")
        pen = True
    return " ".join(d)


def _band_svg(bands, n, Y, h):
    pts_top, pts_bot = [], []
    out = []
    for i, b in enumerate(bands + [None]):
        if b is None:
            if pts_top:
                poly = pts_top + list(reversed(pts_bot))
                out.append('<polygon class="band" points="' + " ".join(f"{x:.1f},{y:.1f}" for x, y in poly) + '"/>')
            pts_top, pts_bot = [], []
            continue
        pts_top.append((_x(i, n), Y(b[1])))
        pts_bot.append((_x(i, n), Y(b[0])))
    return out


def _endlabel(v, n, Y, fmt):
    return f'<text class="endv" x="{W - 2}" y="{Y(v) + 5:.1f}" text-anchor="end">{fmt.format(v)}</text>'


def _metric_chart(days, daily, roll, bands, cls, label, fmt, h=130):
    n = len(days)
    allv = daily + [b[0] for b in bands if b] + [b[1] for b in bands if b]
    if len([v for v in daily if v is not None]) < 5:
        return ""
    lo, hi = _scale(allv)
    Y = lambda v: h - 8 - (h - 16) * (v - lo) / (hi - lo)
    o = _frame(h, n, days, cls, label)
    o += _band_svg(bands, n, Y, h)
    for i, v in enumerate(daily):
        if v is not None:
            o.append(f'<line class="dot" x1="{_x(i, n):.1f}" x2="{_x(i, n):.1f}" y1="{Y(v):.1f}" y2="{Y(v):.1f}"/>')
    o.append(f'<path class="line" d="{_path(roll, n, Y)}"/>')
    o.append("</svg>")
    return "".join(o)


def _load_chart(days, loads, atl, ctl, h=150):
    n = len(days)
    hi = max([x for x in loads if x] + [x for x in atl if x] + [1])
    Y = lambda v: h - 6 - (h - 16) * v / (hi * 1.05)
    o = _frame(h, n, days, "load", "Daily training load with fitness and fatigue")
    bw = (W - LP - RP) / n * 0.7
    for i, v in enumerate(loads):
        if v:
            o.append(f'<rect class="lb" x="{_x(i, n) - bw / 2:.1f}" y="{Y(v):.1f}" width="{bw:.1f}" height="{h - 6 - Y(v):.1f}" rx="1"/>')
    o.append(f'<path class="atl" d="{_path(atl, n, Y)}"/>')
    o.append(f'<path class="ctl" d="{_path(ctl, n, Y)}"/>')
    o.append("</svg>")
    return "".join(o)


def _signed_chart(days, vals, cls, label, h=110, good_up=True, zones=None):
    n = len(days)
    v = [x for x in vals if x is not None]
    if len(v) < 5:
        return ""
    m = max(abs(x) for x in v) or 1
    if zones:
        m = max(m, max(abs(z) for z in zones))
    mid = h / 2
    Y = lambda x: mid - (h / 2 - 8) * x / m
    o = _frame(h, n, days, cls, label)
    o.append(f'<line class="zero" x1="{LP}" x2="{W - RP}" y1="{mid}" y2="{mid}"/>')
    bw = (W - LP - RP) / n * 0.72
    for i, x in enumerate(vals):
        if x is None:
            continue
        good = (x >= 0) == good_up
        y0, y1 = (Y(x), mid) if x >= 0 else (mid, Y(x))
        o.append(f'<rect class="{"pos" if good else "neg"}" x="{_x(i, n) - bw / 2:.1f}" y="{y0:.1f}" '
                 f'width="{bw:.1f}" height="{max(1, y1 - y0):.1f}"/>')
    o.append("</svg>")
    return "".join(o)


def _sleep_chart(days, slept, need, h=130):
    """Each night a bar from 4 h up; green if it met your need, red if short. Need is the dashed line."""
    n = len(days)
    vals = [v for v in slept if v is not None]
    if len(vals) < 5:
        return ""
    lo, hi = 4.0, max(vals + [x for x in need if x] + [9.0]) + 0.2
    Y = lambda v: h - 4 - (h - 10) * (max(lo, v) - lo) / (hi - lo)
    o = _frame(h, n, days, "sleep", "Nightly sleep against your need")
    bw = (W - LP - RP) / n * 0.7
    for i, v in enumerate(slept):
        if v is None:
            continue
        ok = need[i] is None or v >= need[i] - 0.25
        o.append(f'<rect class="{"pos" if ok else "neg"}" x="{_x(i, n) - bw / 2:.1f}" y="{Y(v):.1f}" '
                 f'width="{bw:.1f}" height="{h - 4 - Y(v):.1f}" rx="1"/>')
    o.append(f'<path class="need" d="{_path(need, n, Y)}"/>')
    o.append("</svg>")
    return "".join(o)


def _axis(days):
    """Month labels as HTML, positioned by percentage, so they never stretch."""
    n = len(days)
    out = []
    for i, d in enumerate(days):
        dd = date.fromisoformat(d)
        if (dd.day == 1 and i < n - 8) or i == n - 1:
            left = 100 * _x(i, n) / W
            out.append(f'<span style="left:{left:.2f}%"{" class=\"last\"" if i == n - 1 else ""}>'
                       f'{"Today" if i == n - 1 else dd.strftime("%b")}</span>')
    return '<div class="axis">' + "".join(out) + "</div>"


# --------------------------------------------------------------------------- reading the trends
def build(p) -> Trends:
    end = date.fromisoformat(p.as_of)
    days = [(end - timedelta(days=DAYS - 1 - i)).isoformat() for i in range(DAYS)]
    by = {w.date: w for w in p.wellness}
    ld = {x.date: x for x in p.load_days}

    hrv = [by[d].hrv if d in by and by[d].hrv else None for d in days]
    rhr = [by[d].rhr if d in by and by[d].rhr else None for d in days]
    pre = [(end - timedelta(days=DAYS + 60 - i)).isoformat() for i in range(60)]
    hrv_full = [by[d].hrv if d in by and by[d].hrv else None for d in pre] + hrv
    rhr_full = [by[d].rhr if d in by and by[d].rhr else None for d in pre] + rhr
    hrv_band = _band(hrv_full)[60:]
    rhr_band = _band(rhr_full)[60:]
    hrv7, rhr7 = _roll(hrv), _roll(rhr)
    loads = [p.daily_load.get(d, 0.0) for d in days]
    atl = [ld[d].atl if d in ld else None for d in days]
    ctl = [ld[d].ctl if d in ld else None for d in days]
    tsb = [(ld[d].ctl - ld[d].atl) if d in ld else None for d in days]

    sb = sleepmod.bank(p.wellness, p.daily_load, p.as_of, history_nights=DAYS)
    hist = {n.date: n for n in sb.history}
    sleep_h = [hist[d].slept if d in hist else None for d in days]
    need = [hist[d].need if d in hist else None for d in days]
    debt = [(-hist[d].debt) if d in hist else None for d in days]       # + banked, − owed

    z, _ = _z_by_date(p.wellness)
    r = p.response
    outside = []
    for d in days:
        if d in z and r and r.fitted:
            y = (date.fromisoformat(d) - timedelta(days=1)).isoformat()
            yy = (date.fromisoformat(d) - timedelta(days=2)).isoformat()
            if y in ld and yy in ld and ld[yy].ctl > 5:
                outside.append(z[d] - (r.intercept + r.slope * ld[y].load / ld[yy].ctl))
                continue
        outside.append(None)

    def change(series, back=28):
        a = _avg(series[-7:])
        b = _avg(series[-back - 7:-back])
        return a, b

    rows: list[Row] = []

    # Training load, fitness, fatigue
    lastv = lambda xs, back=0: next((v for v in reversed(xs[:len(xs) - back] if back else xs) if v is not None), None)
    c_now, c_then, a_now = lastv(ctl), lastv(ctl, 28), lastv(atl)
    if c_now:
        fch = _pct(c_now, c_then) or 0
        fatigue_vs = (a_now / c_now - 1) * 100 if a_now else 0
        says = (f"Fitness is {'up' if fch >= 0 else 'down'} {abs(fch):.0f}% in 4 weeks. "
                + ("Fatigue is running well above fitness: you're loading hard right now."
                   if fatigue_vs > 25 else
                   "Fatigue is below fitness: you've been backing off." if fatigue_vs < -15 else
                   "Fatigue is in step with fitness: steady, sustainable load."))
        rows.append(Row("load", "Training load", f"{c_now:.0f} fitness",
                        says, "good" if fch > 3 else "bad" if fch < -5 else "none",
                        _load_chart(days, loads, atl, ctl)))
    form = lastv(tsb)
    if form is not None and c_now:
        rr = form / c_now
        word = ("fresh" if rr > 0.15 else "neither fresh nor tired" if rr > -0.05 else
                "carrying productive fatigue" if rr > -0.3 else "overreached")
        days_neg = 0
        for x in reversed(tsb):
            if x is not None and x < -0.3 * c_now:
                days_neg += 1
            else:
                break
        says = f"You're {word}."
        if days_neg >= 5:
            says += f" {days_neg} days in a row deep in the red: that's how fatigue builds into overreaching."
        rows.append(Row("form", "Fatigue build-up", f"{form:+.0f}", says,
                        "good" if rr > -0.05 else "ok" if rr > -0.3 else "bad",
                        _signed_chart(days, tsb, "form", "Form: fitness minus fatigue", good_up=True)))

    # HRV
    a, b = change(hrv)
    a_r, b_r = change(rhr)
    if a and b:
        ch = _pct(a, b)
        word = "rising" if ch > 4 else "falling" if ch < -4 else "holding steady"
        band_now = hrv_band[-1]
        where = ""
        if band_now:
            where = (" It's above your normal range." if hrv7[-1] and hrv7[-1] > band_now[1] else
                     " It's below your normal range." if hrv7[-1] and hrv7[-1] < band_now[0] else
                     " It's inside your normal range.")
        meaning = {"rising": "Your nervous system is recovering well from what you're doing.",
                   "falling": "Your body is under more stress than it's clearing.",
                   "holding steady": "No sign of building fatigue."}[word]
        rows.append(Row("hrv", "HRV", f"{hrv7[-1]:.0f} ms" if hrv7[-1] else "—",
                        f"{word.capitalize()}: {ch:+.0f}% in 4 weeks.{where} {meaning}",
                        "good" if ch > 4 else "bad" if ch < -4 else "none",
                        _metric_chart(days, hrv, hrv7, hrv_band, "hrv", "Overnight HRV with your normal range", "{:.0f}")))
    if a_r and b_r:
        d_r = a_r - b_r
        word = "creeping up" if d_r >= 2 else "coming down" if d_r <= -2 else "steady"
        meaning = {"creeping up": "Rising resting HR is the earliest sign of fatigue or illness.",
                   "coming down": "A falling resting HR is what fitness looks like.",
                   "steady": "Nothing building."}[word]
        moved = "no change" if abs(d_r) < 0.5 else f"{d_r:+.0f} bpm"
        rows.append(Row("rhr", "Resting HR", f"{rhr7[-1]:.0f} bpm" if rhr7[-1] else "—",
                        f"{word.capitalize()}: {moved} in 4 weeks. {meaning}",
                        "bad" if d_r >= 2 else "good" if d_r <= -2 else "none",
                        _metric_chart(days, rhr, rhr7, rhr_band, "rhr", "Overnight resting heart rate with your normal range", "{:.0f}")))

    # Sleep
    s7, s7_then = change(sleep_h)
    if s7:
        diff = (s7 - s7_then) * 60 if s7_then else 0
        need_now = _avg(need[-7:]) or 8
        short = (need_now - s7) * 60
        says = (f"Averaging {sleepmod._hm(s7)} a night against a need of about {sleepmod._hm(need_now)}"
                + ((", same as a month ago." if abs(diff) < 5 else f", {diff:+.0f} min vs a month ago.") if s7_then else "."))
        says += (" You're short every night, and it adds up." if short > 30 else
                 " That's enough." if short <= 0 else " Close to enough.")
        rows.append(Row("sleep", "Sleep", sleepmod._hm(s7), says,
                        "bad" if short > 30 else "good" if short <= 0 else "ok",
                        _sleep_chart(days, sleep_h, need)))
    if any(x is not None for x in debt):
        cur = sb.balance
        rows.append(Row("debt", "Sleep debt", (f"{sleepmod._hm(cur)} owed" if cur > 0.1 else
                                               f"{sleepmod._hm(-cur)} banked" if cur < -0.1 else "square"),
                        sb.title + ". " + ("It drains slowly: about a third of it clears per good night."
                                           if cur > 1 else "Nothing owed."),
                        sb.tone, _signed_chart(days, debt, "debt", "Sleep banked or owed", good_up=True)))

    # Stress from outside training
    o7 = _avg(outside[-7:])
    if o7 is not None:
        says = ("Over the last week your HRV has been lower than your training explains. Something outside "
                "training is adding stress: sleep, drinks, work, travel, or illness."
                if o7 < -0.5 else
                "Your HRV is doing what your training predicts. Life outside training isn't piling on."
                if o7 < 0.5 else
                "Your HRV is better than your training would predict. Whatever you're doing off the bike is working.")
        rows.append(Row("outside", "Stress outside training", f"{o7:+.1f} SD", says,
                        "bad" if o7 < -0.5 else "good" if o7 >= 0.5 else "none",
                        _signed_chart(days, outside, "outside", "HRV versus what training predicts", good_up=True)))

    # Together: the trend state
    h_ch = _pct(a, b) if a and b else 0
    r_ch = (a_r - b_r) if a_r and b_r else 0
    l_ch = _pct(_avg(loads[-28:]), _avg(loads[-56:-28])) or 0
    if h_ch < -4 and r_ch >= 2:
        head, tone, why = ("Fatigue is building.", "bad",
                           "HRV is falling and resting HR is rising together. That's your body falling behind "
                           "the load. Ease off before it turns into a lost week.")
    elif h_ch < -4 and l_ch < 5:
        head, tone, why = ("Something other than training is wearing on you.", "ok",
                           "HRV is falling but training hasn't gone up. Look at sleep, stress, alcohol, or a bug "
                           "coming on.")
    elif h_ch < -4 and r_ch <= -2 and l_ch > 10:
        head, tone, why = ("Heavy load, and your system is flattening out.", "ok",
                           "HRV and resting HR are both falling under a big block. In very fit people that can be "
                           "saturation, not fitness. A lighter week will tell.")
    elif h_ch > 4 and r_ch <= 0:
        head, tone, why = ("You're absorbing it.", "good",
                           "HRV is up and resting HR is flat or down. The training is turning into fitness.")
    else:
        head, tone, why = ("Steady.", "none",
                           "Nothing is building in either direction. Recovery is keeping up with training.")
    return Trends(head, tone, why, rows, days)


def axis(t: Trends) -> str:
    return _axis(t.days)
