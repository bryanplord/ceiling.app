"""Analysis from the raw second-by-second records only.

Nothing here uses Garmin's own settings or computed values: no training
effect, no zones, no max heart rate setting, no session totals. The one label
used is the sport the person chose on the watch, and only to tell a ride or a
swim from movement on foot. Every number comes from the recorded timestamps, heart rate, distance,
altitude, cadence, power and temperature, and every threshold is a plain
description of the data (30-second blocks, 10-bpm bands, a 3 m altitude
hysteresis), not a model of a typical athlete.
"""
from __future__ import annotations

import math

import numpy as np

BLOCK_S = 30          # analysis resolution
GAP_S = 60            # a jump in timestamps longer than this is a pause
MOVING_M = 10         # a block covering at least this many metres is moving
FAST_MS = 6.0         # on foot, a block faster than this is not running (vehicle, lift, ski)
FENCE_K = 3.0         # "far out": beyond Q3 + 3 IQR (or Q1 - 3 IQR) of the person's own values, on a log scale
CLIMB_FLOOR_MH = 100  # two-minute windows climbing slower than this are not climbing
HR_SHIFT = 8          # bpm change that starts a new segment
SPEED_SHIFT = 0.25    # relative speed change that starts a new segment


def _mean(a):
    a = a[np.isfinite(a)]
    return float(a.mean()) if len(a) else math.nan


def _med(a):
    a = a[np.isfinite(a)]
    return float(np.median(a)) if len(a) else math.nan


def ok(x):
    return x is not None and not (isinstance(x, float) and math.isnan(x))


def r0(x):
    return int(round(x)) if ok(x) else None


def dur_str(seconds):
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# Everything is measured in metres and seconds and written in US units.
MILE = 1609.344
FOOT = 0.3048
YARD = 0.9144


def mi(m):
    return round(m / MILE, 2)


def ft(m):
    return int(round(m / FOOT))


def mph(ms):
    return ms * 3600 / MILE


def pace_str(ms):
    """Minutes per mile."""
    if not ok(ms) or ms < 0.2:
        return None
    sec = int(round(MILE / ms))
    return f"{sec // 60}:{sec % 60:02d}"


def speed_str(mode, ms):
    if not ok(ms) or ms <= 0:
        return ""
    if mode in ("bike", "fast"):
        return f"{mph(ms):.1f} mph"
    if mode == "swim":
        sec = int(round(100 * YARD / ms))
        return f"{sec // 60}:{sec % 60:02d}/100yd"
    p = pace_str(ms)
    return f"{p}/mi" if p else ""


# ---------------------------------------------------------------- altitude

def climb_descent(alt, hysteresis=3.0):
    """Total up and down from an altitude series, ignoring wobbles under 3 m."""
    alt = alt[np.isfinite(alt)]
    if len(alt) < 2:
        return 0.0, 0.0
    up = down = 0.0
    ref = alt[0]
    for a in alt[1:]:
        d = a - ref
        if d >= hysteresis:
            up += d
            ref = a
        elif d <= -hysteresis:
            down -= d
            ref = a
    return up, down


# ------------------------------------------------------------------ blocks

def blocks(rec, family="foot"):
    """Cut the record stream into 30-second blocks, with pauses between them.

    family is 'bike' or 'swim' when the person recorded the session as cycling
    or swimming, otherwise 'foot'."""
    if not rec or len(rec["t"]) < 5:
        return []
    t = rec["t"]
    idx = np.floor(t / BLOCK_S).astype(int)
    out, prev_end, prev_d, prev_a = [], None, None, None
    for g in np.unique(idx):
        sel = idx == g
        tt = t[sel]
        # A block runs from the last record of the block before it (when there
        # is no pause between them) to its own last record. With records every
        # few seconds, starting at the block's own first record would drop the
        # time between records that fall in different blocks.
        if prev_end is not None and float(tt[0]) - prev_end > GAP_S:
            out.append({"pause": True, "start": prev_end, "end": float(tt[0]), "dur": float(tt[0]) - prev_end})
            prev_d = None
            prev_a = None
            start = float(tt[0])
        elif prev_end is not None:
            start = prev_end
        else:
            start = float(tt[0])
        end = float(tt[-1]) if float(tt[-1]) > start else start + 1.0
        prev_end = end
        dur = end - start
        d = rec["dist"][sel]
        fd = d[np.isfinite(d)]
        # Measure from the last distance reading of the block before, so the
        # stretch between records that fall in different blocks is not lost
        # (with records every few seconds that stretch is a large share).
        if len(fd) and prev_d is not None:
            dist = max(0.0, float(fd[-1] - prev_d))
        elif len(fd) >= 2:
            dist = float(fd.max() - fd.min())
        else:
            dist = 0.0
        if len(fd):
            prev_d = float(fd[-1])
        a = rec["alt"][sel]
        fa = a[np.isfinite(a)]
        if len(fa):
            prev_a_next = float(fa[-1])
        else:
            prev_a_next = prev_a
        hr = rec["hr"][sel]
        cad = rec["cad"][sel]
        speed = dist / dur if dur > 0 else 0.0
        # moving: covering ground, or climbing or descending at least 3 m
        # (steep slow climbing covers little ground per block)
        a0 = prev_a if prev_a is not None else (float(fa[0]) if len(fa) else None)
        rise = abs(float(fa[-1]) - a0) if (len(fa) and a0 is not None) else 0.0
        moving = dist >= MOVING_M * dur / BLOCK_S or (3.0 <= rise and dist >= 2.0)
        has_steps = np.isfinite(cad).any() and _med(cad) > 20
        if not moving:
            mode = "still"
        elif family in ("bike", "swim"):
            mode = family
        elif speed >= FAST_MS:
            mode = "fast"
        else:
            mode = "foot"
        out.append({
            "pause": False, "start": start, "end": end, "dur": dur,
            "hr": _mean(hr), "hr_max": float(np.nanmax(hr)) if np.isfinite(hr).any() else math.nan,
            "dist": dist, "speed": speed, "mode": mode,
            "alt_start": prev_a if (prev_a is not None and len(fa)) else (float(fa[0]) if len(fa) else math.nan),
            "alt_end": float(fa[-1]) if len(fa) else math.nan,
            # watch cadence is strides per minute on foot; double it for steps
            "spm": _med(cad) * rec.get("cad_scale", 2) if has_steps and mode == "foot" else math.nan,
            "power": _mean(rec["power"][sel]),
            "temp": _mean(rec["temp"][sel]),
        })
        prev_a = prev_a_next
    for b in out:
        if not b["pause"]:
            b["dalt"] = b["alt_end"] - b["alt_start"] if ok(b["alt_start"]) and ok(b["alt_end"]) else math.nan
    # The climb rate over the two minutes around each block. A two-minute window
    # keeps watches that record altitude in coarse steps from looking like
    # jumps one block at a time. Jumps are marked later, against the person's
    # own spread of these rates (see far_out and mark_jumps).
    for i, b in enumerate(out):
        if b["pause"]:
            continue
        b["alt_jump"] = False
        b["win_rate"] = math.nan
        lo = i
        while lo > 0 and i - lo < 2 and not out[lo - 1]["pause"]:
            lo -= 1
        hi = i
        while hi < len(out) - 1 and hi - i < 2 and not out[hi + 1]["pause"]:
            hi += 1
        win = [x for x in out[lo:hi + 1] if ok(x["dalt"])]
        wd = sum(x["dur"] for x in win)
        if ok(b["dalt"]) and wd > 0:
            b["win_rate"] = sum(x["dalt"] for x in win) / wd * 3600
    return out


def far_out(values, side="hi", k=FENCE_K):
    """The fence beyond which a value is far outside the person's own curve:
    Tukey's far-out fence on a log scale, from their values only."""
    v = sorted(math.log(x) for x in values if ok(x) and x > 0)
    if len(v) < 8:
        return None
    q1, q3 = float(np.percentile(v, 25)), float(np.percentile(v, 75))
    iqr = q3 - q1
    return math.exp(q3 + k * iqr) if side == "hi" else math.exp(q1 - k * iqr)


def climb_rates(all_blocks):
    """Two-minute climbing and descending rates by mode, for setting a person's caps."""
    out = {"foot_up": [], "foot_down": [], "bike_up": []}
    for bl in all_blocks:
        for b in bl:
            r = b.get("win_rate", math.nan)
            if b["pause"] or not ok(r) or b["mode"] in ("fast", "swim"):
                continue
            if b["mode"] == "bike":
                if r >= CLIMB_FLOOR_MH:
                    out["bike_up"].append(r)
            elif r >= CLIMB_FLOOR_MH:
                out["foot_up"].append(r)
            elif r <= -CLIMB_FLOOR_MH:
                out["foot_down"].append(-r)
    return out


def mark_jumps(bl, caps):
    """Mark blocks whose two-minute climb rate is far outside the person's own
    curve as altitude jumps, and leave them (and fast blocks) out of climbing."""
    for b in bl:
        if b["pause"]:
            continue
        r = b.get("win_rate", math.nan)
        if ok(r) and b["mode"] != "fast":
            up = caps.get("bike_up") if b["mode"] == "bike" else caps.get("foot_up")
            down = None if b["mode"] == "bike" else caps.get("foot_down")
            if (up and r > up) or (down and r < -down):
                b["alt_jump"] = True
        if b["mode"] == "fast" or b["alt_jump"]:
            b["dalt"] = math.nan
        b["grade"] = b["dalt"] / b["dist"] if ok(b["dalt"]) and b["dist"] > 20 else math.nan
    return bl


def climb_from_blocks(rec, bl):
    """Climb and descent counted within unbroken stretches of recording, leaving
    out pauses (altitude can jump across a gap), altitude-jump blocks, fast
    blocks (lifts, vehicles) and swims."""
    t, alt = rec["t"], rec["alt"]
    up = down = 0.0
    run = []

    def flush():
        if run:
            a, b = run[0]["start"], run[-1]["end"]
            u, d = climb_descent(alt[(t >= a) & (t < b)])
            nonlocal up, down
            up += u
            down += d
            run.clear()

    for b in bl:
        if b["pause"] or b["mode"] in ("fast", "swim") or b.get("alt_jump"):
            flush()
        else:
            run.append(b)
    flush()
    return up, down


# ---------------------------------------------------------------- segments

def _stats(items):
    bl = [b for b in items if not b["pause"]]
    dur = sum(b["dur"] for b in items)
    seg = {"start_s": round(items[0]["start"]), "dur_s": round(dur)}
    if not bl:
        seg["mode"] = "paused"
        return seg
    modes = {}
    for b in bl:
        modes[b["mode"]] = modes.get(b["mode"], 0) + b["dur"]
    seg["mode"] = max(modes, key=modes.get)
    w = np.array([b["dur"] for b in bl])
    hr = np.array([b["hr"] for b in bl])
    f = np.isfinite(hr)
    seg["hr"] = r0(float(np.average(hr[f], weights=w[f]))) if f.any() else None
    seg["hr_first"] = r0(bl[0]["hr"])
    seg["hr_last"] = r0(bl[-1]["hr"])
    dist = sum(b["dist"] for b in bl)
    mov = sum(b["dur"] for b in bl)
    seg["dist_m"] = round(dist)
    seg["speed_ms"] = round(dist / mov, 3) if mov else None
    dalts = [b["dalt"] for b in bl if ok(b["dalt"])]
    seg["up_m"] = round(sum(x for x in dalts if x > 0))
    seg["down_m"] = round(-sum(x for x in dalts if x < 0))
    if dist > 50 and dalts:
        seg["grade_pct"] = round(sum(dalts) / dist * 100, 1)
    if dalts and mov:
        seg["vert_mh"] = round(sum(dalts) / mov * 3600)
    spm = [b["spm"] for b in bl if ok(b["spm"])]
    if spm:
        seg["spm"] = r0(float(np.median(spm)))
    pw = [b["power"] for b in bl if ok(b["power"])]
    if pw:
        seg["power"] = r0(float(np.mean(pw)))
    return seg


def segments(bl):
    """Split where the data changes: movement mode, heart rate or speed shifts
    and stays shifted for at least two blocks (one minute)."""
    if not bl:
        return []
    segs, cur = [], []

    def flush():
        if cur:
            segs.append(list(cur))
            cur.clear()

    i = 0
    while i < len(bl):
        b = bl[i]
        if b["pause"]:
            flush()
            segs.append([b])
            i += 1
            continue
        if not cur:
            cur.append(b)
            i += 1
            continue
        live = [x for x in cur if not x["pause"]]
        # compare with the last minute of the segment, so a gradual rise in
        # heart rate during one effort does not cut it into pieces
        hr_ref = _mean(np.array([x["hr"] for x in live[-2:]]))
        mov = [x for x in live if x["mode"] != "still"]
        sp_ref = (sum(x["dist"] for x in mov) / sum(x["dur"] for x in mov)) if mov else 0.0
        mode_ref = live[-1]["mode"]

        def differs(x):
            if x["pause"]:
                return True
            if x["mode"] != mode_ref:
                return True
            if ok(x["hr"]) and ok(hr_ref) and abs(x["hr"] - hr_ref) > HR_SHIFT:
                return True
            if x["mode"] != "still" and sp_ref > 0 and abs(x["speed"] - sp_ref) / sp_ref > SPEED_SHIFT:
                return True
            return False

        nxt = bl[i + 1] if i + 1 < len(bl) else None
        if differs(b) and nxt is not None and differs(nxt) and len(cur) >= 2:
            flush()
        cur.append(b)
        i += 1
    flush()
    return [_stats(s) for s in segs]


def _similar(a, b):
    """How different two neighbouring segments are, for merging the closest."""
    if a["mode"] != b["mode"]:
        return 1e9
    hd = abs((a.get("hr") or 0) - (b.get("hr") or 0))
    sa, sb = a.get("speed_ms") or 0, b.get("speed_ms") or 0
    sd = abs(sa - sb) / max(sa, sb, 0.1) * 40
    return hd + sd


def _merge(a, b):
    items_dur = a["dur_s"] + b["dur_s"]
    m = {"start_s": a["start_s"], "dur_s": items_dur, "mode": a["mode"]}
    for k in ("dist_m", "up_m", "down_m"):
        m[k] = (a.get(k) or 0) + (b.get(k) or 0)
    for k in ("hr", "spm", "power"):
        va, vb = a.get(k), b.get(k)
        if va and vb:
            m[k] = round((va * a["dur_s"] + vb * b["dur_s"]) / items_dur)
        else:
            m[k] = va or vb
    m["hr_first"], m["hr_last"] = a.get("hr_first"), b.get("hr_last")
    m["speed_ms"] = round(m["dist_m"] / items_dur, 3) if items_dur else None
    if m["dist_m"] > 50:
        m["grade_pct"] = round((m["up_m"] - m["down_m"]) / m["dist_m"] * 100, 1)
    m["vert_mh"] = round((m["up_m"] - m["down_m"]) / items_dur * 3600) if items_dur else None
    return m


def condense(segs, limit=10):
    """Merge the most similar neighbours until at most `limit` parts remain."""
    parts = [dict(s) for s in segs]
    while len(parts) > limit:
        best, bi = None, None
        for i in range(len(parts) - 1):
            d = _similar(parts[i], parts[i + 1])
            if best is None or d < best:
                best, bi = d, i
        if best is None or best >= 1e9:
            # only different modes remain: merge the shortest into its neighbour
            bi = min(range(len(parts) - 1), key=lambda k: parts[k]["dur_s"] + parts[k + 1]["dur_s"])
            parts[bi + 1]["mode"] = parts[bi]["mode"] if parts[bi]["dur_s"] >= parts[bi + 1]["dur_s"] \
                else parts[bi + 1]["mode"]
        parts[bi:bi + 2] = [_merge(parts[bi], parts[bi + 1])]
    return parts


def repeats(segs):
    """A run of at least three similar-length efforts clearly above the
    stretches between them. Described, not named."""
    live = [s for s in segs if s["mode"] != "paused" and s.get("hr")]
    moving = sum(s["dur_s"] for s in live if s["mode"] in ("foot", "bike", "swim"))
    if len(live) < 6 or moving < 600 or moving > 3 * 3600:
        return None
    hrs = np.array([s["hr"] for s in live])
    med = float(np.median(hrs))
    hi = [i for i, s in enumerate(live) if s["mode"] in ("foot", "bike", "swim")
          and s["hr"] >= med + 8 and 45 <= s["dur_s"] <= 900]
    if len(hi) < 3:
        return None
    md = float(np.median([live[i]["dur_s"] for i in hi]))
    hi = [i for i in hi if 0.5 * md <= live[i]["dur_s"] <= 2 * md]
    # the efforts must alternate with easier stretches: a chain of efforts
    # each followed, within two segments, by the next
    best, chain = [], []
    for i in hi:
        if chain and i - chain[-1] in (2, 3) and \
                min(x["hr"] for x in live[chain[-1] + 1:i]) < live[chain[-1]]["hr"] - 8:
            chain.append(i)
        else:
            chain = [i]
        if len(chain) > len(best):
            best = list(chain)
    if len(best) < 3:
        return None
    hi = best
    gaps = [live[i + 1] for i in hi[:-1]]
    on = [live[i] for i in hi]
    return {"n": len(on), "on_s": int(np.median([s["dur_s"] for s in on])),
            "on_hr": int(np.median([s["hr"] for s in on])),
            "on_speed": float(np.median([s.get("speed_ms") or 0 for s in on])),
            "mode": on[0]["mode"],
            "off_s": int(np.median([s["dur_s"] for s in gaps])),
            "off_hr": int(np.median([s["hr"] for s in gaps]))}


def seg_text(s):
    if s["mode"] == "paused":
        return f"paused {dur_str(s['dur_s'])}"
    bits = []
    if s.get("hr"):
        hr = f"HR {s['hr']}"
        if s.get("hr_first") and s.get("hr_last") and abs(s["hr_last"] - s["hr_first"]) >= 6:
            hr += f" ({s['hr_first']}>{s['hr_last']})"
        bits.append(hr)
    if s["mode"] != "still":
        sp = speed_str(s["mode"], s.get("speed_ms"))
        if sp:
            bits.append(sp)
        if s.get("grade_pct") is not None and abs(s["grade_pct"]) >= 3:
            bits.append(f"{s['grade_pct']:+.0f}%")
        if s.get("vert_mh") and abs(s["vert_mh"]) >= 150:
            bits.append(f"{ft(s['vert_mh']):+d} ft/h")
    return f"{dur_str(s['dur_s'])} {s['mode']}" + (f" ({', '.join(bits)})" if bits else "")


def structure(segs):
    if not segs:
        return ""
    rp = repeats(segs)
    parts = condense(segs, 10)
    line = " > ".join(seg_text(p) for p in parts)
    if rp:
        sp = speed_str(rp["mode"], rp["on_speed"])
        line = (f"repeats: {rp['n']} x {dur_str(rp['on_s'])} at HR ~{rp['on_hr']}"
                f"{', ' + sp if sp else ''}, with ~{dur_str(rp['off_s'])} between at HR ~{rp['off_hr']}"
                f" || {line}")
    return line


# ------------------------------------------------------------ whole-session

def rolling_hr(rec, window_s):
    """Highest and lowest heart rate held for `window_s` seconds."""
    if not rec:
        return None, None
    t, hr = rec["t"], rec["hr"]
    f = np.isfinite(hr)
    t, hr = t[f], hr[f]
    if len(t) < 2 or t[-1] - t[0] < window_s:
        return None, None
    csum = np.concatenate(([0.0], np.cumsum(hr)))
    j = np.searchsorted(t, t + window_s)
    valid = j < len(t)
    i = np.nonzero(valid)[0]
    means = (csum[j[valid]] - csum[i]) / (j[valid] - i)
    return float(means.max()), float(means.min())


def fastest(rec, meters, mode_ok):
    """Fastest time over a distance, counting only stretches the blocks call
    the given mode."""
    t, d = rec["t"], rec["dist"]
    f = np.isfinite(d) & mode_ok
    if f.sum() < 10:
        return None
    tt, dd = t[f], d[f]
    if dd[-1] - dd[0] < meters:
        return None
    j = np.searchsorted(dd, dd + meters)
    v = j < len(dd)
    if not v.any():
        return None
    times = tt[j[v]] - tt[v]
    span_ok = times < meters / 0.3  # ignore spans that cross long stops
    if not span_ok.any():
        return None
    return float(times[span_ok].min())


def hr_bands(bl):
    """Minutes spent in each 10-bpm band, moving or not."""
    out = {}
    for b in bl:
        if b["pause"] or not ok(b["hr"]):
            continue
        k = int(b["hr"] // 10 * 10)
        out[k] = out.get(k, 0.0) + b["dur"] / 60
    return {k: round(v, 1) for k, v in sorted(out.items())}
