"""Where your time goes, where it should go, and how to move it.

Heart rate: your own zones (anchored on your measured thresholds). Time is counted
on a 30-second average so a 10-second spike on a steep pitch doesn't count as a
minute of threshold work. Power: Coggan zones on your FTP, pedaling time only.

Target mix for someone training 7+ hours a week across endurance sports: about 80%
below the aerobic threshold, little in the middle, a small dose of real intensity
(Seiler; Stöggl & Sperlich 2014; Neal 2013). It's a starting rule, said as one.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np

from .hr import clean, rolling_mean_full

HR_TARGET = {"Z1 Recovery": 0.25, "Z2 Aerobic": 0.53, "Z3 Tempo": 0.07, "Z4 Threshold": 0.10, "Z5 Max": 0.05}
from .hr import ZONE_LABEL
HR_FEEL = {"Z1 Recovery": "barely working", "Z2 Aerobic": "all day, full sentences",
           "Z3 Tempo": "comfortably hard", "Z4 Threshold": "hard, a few words", "Z5 Max": "all out"}
PWR_ZONES = [("Z1 Recovery", 0.0, 0.55), ("Z2 Endurance", 0.55, 0.75), ("Z3 Tempo", 0.75, 0.90),
             ("Z4 Threshold", 0.90, 1.05), ("Z5 VO2", 1.05, 1.20), ("Z6 Anaerobic", 1.20, 9.0)]
PWR_TARGET = {"Z1 Recovery": 0.20, "Z2 Endurance": 0.55, "Z3 Tempo": 0.08, "Z4 Threshold": 0.09,
              "Z5 VO2": 0.05, "Z6 Anaerobic": 0.03}
PWR_FEEL = {"Z1 Recovery": "spinning", "Z2 Endurance": "all day", "Z3 Tempo": "comfortably hard",
            "Z4 Threshold": "an hour, all-out", "Z5 VO2": "3–8 minutes", "Z6 Anaerobic": "under a minute"}
WEEKS = 16
_cache: dict = {}


@dataclass
class ZoneRow:
    key: str
    name: str
    range: str
    feel: str
    now_h: float          # hours a week, last 4 weeks
    target_h: float
    share: float
    target_share: float
    color: int            # 1..6


@dataclass
class Distribution:
    kind: str                       # "hr" | "power"
    rows: list[ZoneRow]
    weekly_h: float
    weeks: list[tuple[str, list[float]]]    # (week start, hours per zone)
    verdict: str = ""
    moves: list[str] = field(default_factory=list)
    basis: str = ""
    easy_keys: tuple = ()


def _week(d: str) -> str:
    x = date.fromisoformat(d)
    return (x - timedelta(days=x.weekday())).isoformat()


def _hr_secs(store, aid: str, bounds) -> list[float] | None:
    key = ("hr", aid, tuple(b[2] for b in bounds))
    if key not in _cache:
        st = store.stream(aid)
        if not st:
            _cache[key] = None
        else:
            h = clean(st[0], st[1])
            if len(h) >= 30:
                filled = np.where(np.isnan(h), np.nanmean(h) if np.any(~np.isnan(h)) else 0, h)
                sm = rolling_mean_full(filled, 30)
                sm = sm[~np.isnan(h[29:])]
            else:
                sm = h[~np.isnan(h)]
            out = []
            for i, (_, lo, hi) in enumerate(bounds):
                lo_ = -1 if i == 0 else lo
                hi_ = 999 if i == len(bounds) - 1 else hi
                out.append(float(np.sum((sm >= lo_) & (sm <= hi_ + 0.999))))
            _cache[key] = out
    return _cache[key]


def _pwr_secs(store, aid: str, ftp: float) -> list[float] | None:
    key = ("pw", aid, round(ftp))
    if key not in _cache:
        st = store.power(aid)
        if not st:
            _cache[key] = None
        else:
            w = np.asarray([np.nan if v is None else v for v in st[1]], float)
            w = w[~np.isnan(w) & (w > 0)]          # pedaling time only
            _cache[key] = [float(np.sum((w >= lo * ftp) & (w < hi * ftp))) for _, lo, hi in PWR_ZONES]
    return _cache[key]


def _build(kind, names, ranges, feels, targets, per_act, acts, end: date, colors, easy_keys, basis):
    start = end - timedelta(weeks=WEEKS)
    weeks: dict[str, list[float]] = {}
    w = end - timedelta(days=end.weekday())
    for i in range(WEEKS):
        weeks[(w - timedelta(weeks=WEEKS - 1 - i)).isoformat()] = [0.0] * len(names)
    last4 = [0.0] * len(names)
    cut4 = (end - timedelta(days=28)).isoformat()
    for a in acts:
        if a.date < start.isoformat() or a.date > end.isoformat():
            continue
        secs = per_act(a)
        if not secs:
            continue
        wk = _week(a.date)
        if wk in weeks:
            weeks[wk] = [x + s / 3600 for x, s in zip(weeks[wk], secs)]
        if a.date > cut4:
            last4 = [x + s / 3600 / 4 for x, s in zip(last4, secs)]
    total = sum(last4)
    if total < 0.5:
        return None
    rows = [ZoneRow(k, ZONE_LABEL.get(k, k.split(" ", 1)[1]), ranges[i], feels[i], last4[i], targets[k] * total,
                    last4[i] / total, targets[k], colors[i]) for i, k in enumerate(names)]
    return Distribution(kind, rows, total, sorted(weeks.items()), basis=basis, easy_keys=easy_keys)


def hr_distribution(store, p) -> Distribution | None:
    if not p.zones:
        return None
    b = p.zones.bounds
    names = [n for n, _, _ in b]
    ranges = [f"under {hi + 1}" if i == 0 else f"{lo}+" if i == len(b) - 1 else f"{lo}–{hi}"
              for i, (_, lo, hi) in enumerate(b)]
    end = date.fromisoformat(p.as_of) - timedelta(days=1)
    d = _build("hr", names, ranges, [HR_FEEL.get(n, "") for n in names], HR_TARGET,
               lambda a: _hr_secs(store, a.id, b), p.activities, end, [1, 2, 3, 4, 5],
               ("Z1 Recovery", "Z2 Aerobic"),
               "Your zones, from your own threshold and aerobic threshold. Time counted on a 30-second average.")
    if d:
        _hr_moves(d, p)
    return d


def ftp_estimate(store, p) -> tuple[float | None, str]:
    rides = sorted([a for a in p.activities if a.sport == "bike"], key=lambda a: a.date, reverse=True)
    for a in rides[:40]:
        f = store.raw(a.id).get("icu_ftp")
        if f:
            return float(f), f"FTP {int(f)} W, from your intervals.icu settings."
    best = 0.0
    cut = (date.fromisoformat(p.as_of) - timedelta(days=90)).isoformat()
    for a in rides:
        if a.date < cut:
            break
        st = store.power(a.id)
        if st and len(st[1]) >= 1200:
            w = np.asarray([0 if v is None else v for v in st[1]], float)
            m = rolling_mean_full(w, 1200)
            if len(m):
                best = max(best, float(np.nanmax(m)))
    if best:
        return 0.95 * best, f"FTP about {int(0.95 * best)} W: 95% of your best 20 minutes in the last 90 days."
    return None, ""


def power_distribution(store, p) -> Distribution | None:
    ftp, basis = ftp_estimate(store, p)
    if not ftp:
        return None
    names = [n for n, _, _ in PWR_ZONES]
    ranges = [f"under {round(hi * ftp)} W" if i == 0 else f"{round(lo * ftp)} W+" if i == len(PWR_ZONES) - 1
              else f"{round(lo * ftp)}–{round(hi * ftp)} W" for i, (_, lo, hi) in enumerate(PWR_ZONES)]
    end = date.fromisoformat(p.as_of) - timedelta(days=1)
    d = _build("power", names, ranges, [PWR_FEEL[n] for n in names], PWR_TARGET,
               lambda a: _pwr_secs(store, a.id, ftp) if a.sport == "bike" else None,
               p.activities, end, [1, 2, 3, 4, 5, 6], ("Z1 Recovery", "Z2 Endurance"),
               basis + " Pedaling time only, on rides with power.")
    if d:
        _pwr_moves(d, ftp)
    return d


def _easy_share(d: Distribution) -> float:
    return sum(r.share for r in d.rows if r.key in d.easy_keys)


def _hr_moves(d: Distribution, p) -> None:
    R = {r.key: r for r in d.rows}
    z = p.zones
    easy = _easy_share(d) * 100
    mid = R["Z3 Tempo"]
    hard = R["Z4 Threshold"].now_h + R["Z5 Max"].now_h
    hard_t = R["Z4 Threshold"].target_h + R["Z5 Max"].target_h
    d.verdict = (f"{easy:.0f}% of your time is easy. The target is about 80%." if easy < 72 else
                 f"{easy:.0f}% of your time is easy. That's right where it should be." if easy <= 88 else
                 f"{easy:.0f}% of your time is easy. You're leaving fitness on the table with almost no intensity.")
    rec, aer = R["Z1 Recovery"], R["Z2 Aerobic"]
    if easy >= 72 and rec.now_h > aer.now_h and rec.now_h - rec.target_h >= 0.5:
        d.verdict = (f"{easy:.0f}% of your time is easy, which is right. But most of it is too easy: "
                     f"{rec.now_h:.1f} hours a week barely working, only {aer.now_h:.1f} in Aerobic, where fitness is built.")
        d.moves.append(f"Turn {rec.now_h - rec.target_h:.1f} hours a week of barely-working into Aerobic "
                       f"({aer.range}). Same rides, a touch more push: you can still talk in full sentences, "
                       f"but you're breathing. Under {rec.range.split()[-1]} is recovery, not training.")
    if mid.now_h - mid.target_h >= 0.4:
        d.moves.append(f"Move {mid.now_h - mid.target_h:.1f} hours a week out of Tempo ({mid.range}). That's the "
                       f"'kind of hard' riding: pushing climbs because you can. Spin them under "
                       f"{z.top('Z2 Aerobic')} instead, even if it means walking the steepest pitch.")
    a = R["Z2 Aerobic"]
    if a.target_h - a.now_h >= 0.4 and not any(m.startswith("Turn") for m in d.moves):
        d.moves.append(f"Put that time into Aerobic ({a.range}): long, chatty rides. The easiest way is one "
                       f"ride a week of 2½–3 hours that stays conversational the whole way.")
    if hard_t - hard >= 0.3:
        d.moves.append(f"Add one hard session a week: 4 × 8 minutes at {z.bottom('Z4 Threshold')}–"
                       f"{z.top('Z4 Threshold')}. On the mountain bike, a steady 8-minute climb at that "
                       f"effort counts. Never two hard days in a row.")
    elif hard - hard_t >= 0.5:
        d.moves.append(f"Ease off the hard stuff: {hard:.1f} hours a week above threshold is more than you can "
                       f"absorb. Two hard sessions a week, at least 48 hours apart, is plenty.")
    if not d.moves:
        d.moves.append("Your mix is right. Keep it: grow the easy hours before you add more intensity.")


def _pwr_moves(d: Distribution, ftp: float) -> None:
    R = {r.key: r for r in d.rows}
    easy = _easy_share(d) * 100
    d.verdict = f"{easy:.0f}% of your pedaling is easy (under {round(0.75 * ftp)} W). The target is about 75%."
    mid = R["Z3 Tempo"]
    if mid.now_h - mid.target_h >= 0.3:
        d.moves.append(f"Less Tempo ({mid.range}). On climbs, shift down and keep it under {round(0.75 * ftp)} W.")
    if R["Z2 Endurance"].target_h - R["Z2 Endurance"].now_h >= 0.3:
        d.moves.append(f"More Endurance ({R['Z2 Endurance'].range}): the watts you could hold all day.")
    hard = R["Z4 Threshold"].now_h + R["Z5 VO2"].now_h
    if R["Z4 Threshold"].target_h + R["Z5 VO2"].target_h - hard >= 0.25:
        d.moves.append(f"One quality session a week: 3 × 10 minutes at {round(0.92 * ftp)}–{round(1.0 * ftp)} W, "
                       f"or 5 × 4 minutes at {round(1.08 * ftp)}–{round(1.15 * ftp)} W.")
    if not d.moves:
        d.moves.append("Your power mix is right. Keep building the endurance hours.")


def trend_chart(d: Distribution, w: int = 960, h: int = 300) -> str:
    """16 weeks, each a column split by zone share, with the 80% easy line drawn across.
    Hours a week sit on top, so you see both how much and how hard."""
    weeks = d.weeks
    n = len(weeks)
    L, R, T, B = 40, 10, 34, h - 30
    cw = (w - L - R) / n
    o = [f'<svg class="trend" viewBox="0 0 {w} {h}" width="100%" role="img" '
         f'aria-label="Share of time in each zone, week by week, for {n} weeks">']
    for frac in (0, 0.5, 1):
        y = B - (B - T) * frac
        o.append(f'<text class="tk" x="{L - 8}" y="{y + 5:.0f}" text-anchor="end">{int(frac * 100)}%</text>')
    for i, (wk, hrs) in enumerate(weeks):
        x = L + i * cw + cw * 0.12
        bw = cw * 0.76
        tot = sum(hrs)
        if tot <= 0:
            o.append(f'<rect class="empty" x="{x:.1f}" y="{T}" width="{bw:.1f}" height="{B - T}" rx="3"/>')
        else:
            y = B
            for zi, hz in enumerate(hrs):
                hgt = (B - T) * hz / tot
                if hgt > 0:
                    o.append(f'<rect class="z{d.rows[zi].color}" x="{x:.1f}" y="{y - hgt:.1f}" width="{bw:.1f}" height="{hgt:.1f}"/>')
                y -= hgt
            o.append(f'<text class="hrs" x="{x + bw / 2:.1f}" y="{T - 10}" text-anchor="middle">{tot:.0f}h</text>')
        if i % 4 == 0 or i == n - 1:
            o.append(f'<text class="tk" x="{x + bw / 2:.1f}" y="{h - 8}" text-anchor="middle">'
                     f'{date.fromisoformat(wk).strftime("%b %-d")}</text>')
    easy_t = sum(v for k, v in (HR_TARGET if d.kind == "hr" else PWR_TARGET).items() if k in d.easy_keys)
    y = B - (B - T) * easy_t
    o.append(f'<line class="target" x1="{L}" x2="{w - R}" y1="{y:.1f}" y2="{y:.1f}"/>')
    o.append("</svg>")
    return "".join(o)


def hr_histogram(store, p, days: int = 28) -> tuple[np.ndarray, float | None]:
    """Seconds spent at each bpm (index = bpm) over the last `days`, and the median
    average HR of those sessions: where your heart actually lives when you train."""
    end = date.fromisoformat(p.as_of)
    cut = (end - timedelta(days=days)).isoformat()
    hist = np.zeros(231)
    avgs = []
    for a in p.activities:
        if a.date < cut or a.date >= p.as_of or a.sport == "walk":
            continue
        key = ("hist", a.id)
        if key not in _cache:
            st = store.stream(a.id)
            if not st:
                _cache[key] = None
            else:
                h = clean(st[0], st[1])
                h = h[~np.isnan(h)].astype(int)
                _cache[key] = np.bincount(np.clip(h, 0, 230), minlength=231)
        c = _cache[key]
        if c is not None:
            hist += c
        if a.avg_hr:
            avgs.append(a.avg_hr)
    return hist, (float(np.median(avgs)) if avgs else None)


def hr_window(store, p, day: str, days: int | None) -> np.ndarray:
    """Seconds at each bpm across training sessions in the last `days` days INCLUDING `day`
    (None = all time). Shares the per-session cache with hr_histogram."""
    cut = (date.fromisoformat(day) - timedelta(days=days - 1)).isoformat() if days else ""
    hist = np.zeros(231)
    for a in p.activities:
        if a.date < cut or a.date > day or a.sport == "walk":
            continue
        key = ("hist", a.id)
        if key not in _cache:
            st = store.stream(a.id)
            if not st:
                _cache[key] = None
            else:
                h = clean(st[0], st[1])
                h = h[~np.isnan(h)].astype(int)
                _cache[key] = np.bincount(np.clip(h, 0, 230), minlength=231)
        c = _cache[key]
        if c is not None:
            hist += c
    return hist
