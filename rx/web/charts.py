"""Small charts for Today's cards.

target_svg: today's range drawn on your own zones (only the ones near it), the aim dashed, the
ceiling red, and the time axis as a minimum and a maximum.
trace_svg: a finished session's heart rate against the same zones and ceiling; time over the
ceiling drawn red.
Both scale to the card (viewBox, width 100%)."""
from __future__ import annotations

import re

import numpy as np

INK, GRAPH, MIST_TXT, RED, STOP = "#0a0a0a", "#6e6e73", "#5e5e63", "#ff3b30", "#c4281f"
BAND_FILL = {"z0": "#e9e5f4", "z1": "#dce6f5", "z2": "#d6ebdf", "z3": "#f6edd3", "z4": "#f7e2d3", "z5": "#f3d6d4"}
FONT = 'font-family="Archivo, sans-serif"'


def bands(p) -> list[tuple[str, str, int, int]]:
    """(name, class, from, to) the way the big heart chart names them: Recovery split off the bottom
    of zone 1, the rest of zone 1 is Endurance."""
    from ..analysis.hr import ZONE_LABEL
    z = p.zones
    if not z:
        return []
    z1top = z.top("Z1 Recovery")
    rec_top = min(round(0.68 * z.lthr), z1top - 6)
    out = [("Recovery", "z0", 0, rec_top + 1), ("Endurance", "z1", rec_top + 1, z1top + 1)]
    for i, (name, zlo, zhi) in enumerate(z.bounds[1:], start=2):
        out.append((ZONE_LABEL.get(name, name), f"z{i}", zlo, 999 if i == len(z.bounds) else zhi + 1))
    return out


def aim(rx) -> int | None:
    m = re.search(r"Hold (\d+)", rx.how_short or "")
    if m:
        return int(m.group(1))
    if rx.hr_ceiling is None:
        return None
    return round(((rx.hr_low or rx.hr_ceiling - 10) + rx.hr_ceiling) / 2)


def maximum_minutes(rx) -> int | None:
    m = re.match(r"(\d+) minutes", rx.maximum or "")
    return int(m.group(1)) if m else None


def minimum_minutes(rx) -> int | None:
    m = re.search(r"(\d+)-minute", rx.minimum or "")
    return int(m.group(1)) if m else None


def _zone_layer(o, p, Y, w, ph, lo, hi, fs, ceil):
    for name, cls, z0, z1 in bands(p):
        if z1 <= lo or z0 >= hi:
            continue
        y0, y1 = Y(min(z1, hi)), Y(max(z0, lo))
        o.append(f'<rect x="0" y="{y0:.1f}" width="{w}" height="{y1 - y0:.1f}" fill="{BAND_FILL[cls]}"/>')
        if y1 - y0 >= fs + 6:
            rng = f"under {z1}" if z0 == 0 else (f"{z0}+" if z1 >= 999 else f"{z0}–{z1 - 1}")
            o.append(f'<text x="8" y="{y0 + fs + 5:.1f}" {FONT} font-size="{fs}" fill="{MIST_TXT}" paint-order="stroke" stroke="#fff" stroke-opacity=".7" stroke-width="3">'
                     f'<tspan font-weight="700" fill="{INK}">{name}</tspan> {rng}</text>')


def target_svg(rx, p, w: int = 330, h: int = 290) -> str:
    try:
        return _target_svg(rx, p, w, h)
    except Exception as e:
        print(f"target chart failed: {e!r}")
        return ""


def _target_svg(rx, p, w: int = 330, h: int = 290) -> str:
    c, lo_ = rx.hr_ceiling, rx.hr_low
    if c is None or not p.zones:
        return ""
    lo_ = lo_ if lo_ is not None else c - 10
    a = aim(rx)
    plan = rx.max_minutes or 60
    mn = minimum_minutes(rx)
    mx = max(plan, maximum_minutes(rx) or plan)
    span = mx * 1.12
    axh = 28
    ph = h - axh
    lo, hi = lo_ - 22, c + 18
    fs = 12

    def Y(v): return (hi - max(lo, min(hi, v))) / (hi - lo) * ph
    def X(m): return m / span * w
    xpl, xmx = X(plan), X(mx)
    o = [f'<svg class="tgt" viewBox="0 0 {w} {h}" role="img" aria-label="Today on your zones: '
         f'{"aim for " + str(a) + ", " if a else ""}stay {lo_} to {c}, never over {c}. '
         f'{"At least " + str(mn) + " minutes, " if mn else ""}{plan} planned, {mx} at most.">']
    _zone_layer(o, p, Y, w, ph, lo, hi, fs, c)
    o.append(f'<rect x="0" y="{Y(c):.1f}" width="{xpl:.1f}" height="{Y(lo_) - Y(c):.1f}" fill="#4a7fd4" opacity=".24"/>')
    if xmx > xpl + 1:
        o.append(f'<rect x="{xpl:.1f}" y="{Y(c):.1f}" width="{xmx - xpl:.1f}" height="{Y(lo_) - Y(c):.1f}" fill="#4a7fd4" opacity=".1"/>')
    if a:
        o.append(f'<line x1="0" y1="{Y(a):.1f}" x2="{xpl:.1f}" y2="{Y(a):.1f}" stroke="{INK}" stroke-width="1.25" stroke-dasharray="5 5"/>')
        o.append(f'<text x="{xpl - 6:.1f}" y="{Y(a) - 6:.1f}" text-anchor="end" {FONT} font-size="12" font-weight="700" fill="{INK}" paint-order="stroke" stroke="#fff" stroke-opacity=".7" stroke-width="3">Aim {a}</text>')
    o.append(f'<rect x="{xmx:.1f}" y="0" width="{w - xmx:.1f}" height="{ph}" fill="#fff" opacity=".6"/>')
    o.append(f'<line x1="0" y1="{Y(c):.1f}" x2="{w}" y2="{Y(c):.1f}" stroke="{RED}" stroke-width="2.5"/>')
    o.append(f'<text x="{xpl - 6:.1f}" y="{Y(c) - 7:.1f}" text-anchor="end" {FONT} font-size="17" font-weight="800" font-stretch="125%" fill="{STOP}">{c}</text>')
    o.append(f'<line x1="{xmx:.1f}" y1="0" x2="{xmx:.1f}" y2="{ph}" stroke="{INK}" stroke-width="1.25"/>')
    if xmx > xpl + 1:
        o.append(f'<line x1="{xpl:.1f}" y1="{Y(c):.1f}" x2="{xpl:.1f}" y2="{ph}" stroke="{INK}" stroke-width="1" stroke-dasharray="3 3"/>')
        o.append(f'<text x="{xmx - 5:.1f}" y="13" text-anchor="end" {FONT} font-size="12" font-weight="700" fill="{INK}" paint-order="stroke" stroke="#fff" stroke-opacity=".7" stroke-width="3">Max {mx}</text>')
    o.append(f'<line x1="0" y1="{ph}" x2="{w}" y2="{ph}" stroke="#c7c7cc"/>')
    ticks = [(mn, f"Min {mn} min")] if mn else []
    ticks.append((plan, f"{plan} min" if xmx > xpl + 1 else f"Max {plan} min"))
    for m, t in ticks:
        x = X(m)
        o.append(f'<line x1="{x:.1f}" y1="{ph}" x2="{x:.1f}" y2="{ph + 6}" stroke="{INK}" stroke-width="1.25"/>')
        anchor = "end" if x > w - 40 else "middle"
        o.append(f'<text x="{min(x, w):.1f}" y="{ph + 20}" text-anchor="{anchor}" {FONT} font-size="13" font-weight="700" fill="{INK}">{t}</text>')
    o.append("</svg>")
    return "".join(o)


def trace(stream, points: int = 90) -> list[tuple[float, float]]:
    """A session's heart rate, smoothed to about a minute, as (minute, bpm)."""
    from ..analysis.hr import clean
    if not stream:
        return []
    t = np.asarray([float(x) for x in stream[0]])
    h = clean(stream[0], stream[1])
    ok = ~np.isnan(h)
    if ok.sum() < 60:
        return []
    t, h = t[ok], h[ok]
    edges = np.linspace(t[0], t[-1], points + 1)
    out = []
    for i in range(points):
        m = (t >= edges[i]) & (t < edges[i + 1])
        if m.any():
            out.append((float((edges[i] + edges[i + 1]) / 2 - t[0]) / 60, float(np.mean(h[m]))))
    return out


def trace_svg(tr, p, ceiling: int | None, w: int = 330, h: int = 250, labels: bool = True) -> str:
    try:
        return _trace_svg(tr, p, ceiling, w, h, labels)
    except Exception as e:
        print(f"trace chart failed: {e!r}")
        return ""


def _trace_svg(tr, p, ceiling: int | None, w: int = 330, h: int = 250, labels: bool = True) -> str:
    if not tr or ceiling is None or not p.zones:
        return ""
    vals = [v for _, v in tr]
    lo, hi = min(vals) - 6, max(max(vals), ceiling) + 10
    end = tr[-1][0] or 1
    ph = h - 24
    fs = 12

    def Y(v): return (hi - max(lo, min(hi, v))) / (hi - lo) * ph
    def X(m): return m / end * w
    peak = max(tr, key=lambda x: x[1])
    over = sum(1 for _, v in tr if v > ceiling) / len(tr)
    o = [f'<svg class="tgt" viewBox="0 0 {w} {h}" role="img" aria-label="Heart rate through the session: '
         f'{round((1 - over) * 100)} percent under {ceiling}, peak {round(peak[1])}.">',
         f'<defs><clipPath id="ovc"><rect x="0" y="-20" width="{w}" height="{Y(ceiling) + 20:.1f}"/></clipPath></defs>']
    if labels:
        _zone_layer(o, p, Y, w, ph, lo, hi, fs, ceiling)
    else:                                   # small version: the colours alone, no words to collide
        for name, cls, z0, z1 in bands(p):
            if z1 <= lo or z0 >= hi:
                continue
            y0, y1 = Y(min(z1, hi)), Y(max(z0, lo))
            o.append(f'<rect x="0" y="{y0:.1f}" width="{w}" height="{y1 - y0:.1f}" fill="{BAND_FILL[cls]}"/>')
    pts = " ".join(f"{X(m):.1f},{Y(v):.1f}" for m, v in tr)
    o.append(f'<polyline points="{pts}" fill="none" stroke="{INK}" stroke-width="2.25" stroke-linejoin="round" stroke-linecap="round"/>')
    o.append(f'<polyline points="{pts}" fill="none" stroke="{RED}" stroke-width="3.5" stroke-linejoin="round" stroke-linecap="round" clip-path="url(#ovc)"/>')
    o.append(f'<line x1="0" y1="{Y(ceiling):.1f}" x2="{w}" y2="{Y(ceiling):.1f}" stroke="{RED}" stroke-width="2"/>')
    o.append(f'<text x="6" y="{Y(ceiling) - 7:.1f}" {FONT} font-size="17" font-weight="800" font-stretch="125%" fill="{STOP}" paint-order="stroke" stroke="#fff" stroke-opacity=".8" stroke-width="3">{ceiling}</text>')
    if peak[1] > ceiling + 2:
        px = min(max(X(peak[0]), 16), w - 16)
        o.append(f'<text x="{px:.1f}" y="{max(12, Y(peak[1]) - 8):.1f}" text-anchor="middle" {FONT} font-size="12" font-weight="700" fill="{STOP}" paint-order="stroke" stroke="#fff" stroke-width="3">{round(peak[1])}</text>')
    o.append(f'<line x1="0" y1="{ph}" x2="{w}" y2="{ph}" stroke="#c7c7cc"/>')
    o.append(f'<text x="0" y="{ph + 18}" {FONT} font-size="12" fill="{GRAPH}">0</text>')
    o.append(f'<text x="{w}" y="{ph + 18}" text-anchor="end" {FONT} font-size="12" fill="{GRAPH}">{round(end)} min</text>')
    o.append("</svg>")
    return "".join(o)
