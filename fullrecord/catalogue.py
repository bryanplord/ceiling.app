"""One card per session from its raw records, plus the weekly totals, what
each heart rate bought, and the heart-rate envelope, for the whole record.

Uses only the second-by-second record stream (time, heart rate, distance,
altitude, cadence, power, temperature). The one label used is the sport the
person chose on the watch, to tell a ride or a swim from movement on foot.
Deterministic; no AI.
"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict
from datetime import date, timedelta

import fitparse
import numpy as np

from .checks import check_record, credible_peak, hr_coverage
from .raw import (blocks, climb_from_blocks, far_out, fastest, hr_bands, mark_jumps,
                  ok, person_caps, r0, rolling_hr, segments, structure)

def _fit_file_type(path):
    """The file_id type the device wrote into the file (activity, monitoring and so on).
    This says what kind of file it is, not what the person did, so it is not a label
    in the sense of the rules."""
    try:
        for m in fitparse.FitFile(path).get_messages("file_id"):
            return str(m.get_values().get("type"))
    except Exception:
        return None
    return None


FAMILY = {"cycling": "bike", "e_biking": "bike", "swimming": "swim"}
# Card values checked against the person's own curve: "hi" values are removed
# when far above it, "lo" (times) when far below it. Every limit is computed
# from that person's own sessions; nobody is compared with anyone else.
CHECKED = {"climb_vert_mh": "hi", "bike_climb_vert_mh": "hi", "foot_flat_speed_ms": "hi",
           "bike_flat_speed_ms": "hi", "fastest_foot_1mi_s": "lo", "fastest_foot_3mi_s": "lo",
           "fastest_bike_10mi_s": "lo"}


def _sustained(b, grade):
    """True when the two minutes around a block are steep too. A single steep
    30-second block among flat ones is usually an altitude reading stepping,
    not a climb, and counting only such blocks would make climbing look fast."""
    wd, wa = b.get("win_dist") or 0.0, b.get("win_dalt")
    return ok(wa) and wd > 20 and wa / wd >= grade


def session_card(act, tz, bl):
    rec = act["rec"]
    family = act["family"]
    if not bl:
        return None, None, None
    first_ts = act["first_ts"]
    loc = first_ts.astimezone(tz)
    live = [b for b in bl if not b["pause"]]
    by_mode = defaultdict(float)
    for b in live:
        by_mode[b["mode"]] += b["dur"] / 60
    span = (rec["t"][-1] - rec["t"][0]) / 60
    paused = sum(b["dur"] for b in bl if b["pause"]) / 60
    dist = sum(b["dist"] for b in live)
    up, down = climb_from_blocks(rec, bl)
    moving = [b for b in live if b["mode"] != "still"]
    mhr = np.array([b["hr"] for b in moving]) if moving else np.array([b["hr"] for b in live])
    w = np.array([b["dur"] for b in (moving or live)])
    f = np.isfinite(mhr)
    _, low60 = rolling_hr(rec, 60)
    _, low300 = rolling_hr(rec, 300)
    peak60, peak_notes = credible_peak(rec, 60)
    card = {
        "id": loc.strftime("%Y-%m-%d-%H-%M-%S"),
        "recorded_as": act["sport"] if act["sub_sport"] in ("generic", "None") else f"{act['sport']} ({act['sub_sport']})",
        "date": loc.date().isoformat(),
        "weekday": loc.strftime("%a"),
        "start": loc.strftime("%H:%M"),
        "span_min": round(span, 1),
        "paused_min": round(paused, 1),
        "minutes": {k: round(v, 1) for k, v in by_mode.items()},
        "distance_km": round(dist / 1000, 2),
        "up_m": round(up),
        "down_m": round(down),
        "hr_moving": r0(float(np.average(mhr[f], weights=w[f]))) if f.any() else None,
        "hr_peak_1min": r0(peak60),
        "hr_low_1min": r0(low60),
        "hr_low_5min": r0(low300),
        "hr_bands_min": hr_bands(bl),
    }
    foot = [b for b in live if b["mode"] == "foot"]
    bike = [b for b in live if b["mode"] == "bike"]
    swim = [b for b in live if b["mode"] == "swim"]
    if foot:
        fd = sum(b["dist"] for b in foot)
        ft = sum(b["dur"] for b in foot)
        card["foot_speed_ms"] = round(fd / ft, 3) if ft else None
        flat = [b for b in foot if ok(b["grade"]) and abs(b["grade"]) <= 0.02]
        if len(flat) >= 4:
            card["foot_flat_speed_ms"] = round(sum(b["dist"] for b in flat) / sum(b["dur"] for b in flat), 3)
            fh = [b["hr"] for b in flat if ok(b["hr"])]
            card["foot_flat_hr"] = r0(float(np.mean(fh))) if fh else None
        steep = [b for b in foot if ok(b["grade"]) and b["grade"] >= 0.08 and _sustained(b, 0.08)]
        if len(steep) >= 4:
            card["climb_vert_mh"] = round(sum(b["dalt"] for b in steep) / sum(b["dur"] for b in steep) * 3600)
            sh = [b["hr"] for b in steep if ok(b["hr"])]
            card["climb_hr"] = r0(float(np.mean(sh))) if sh else None
        spm = [b["spm"] for b in foot if ok(b["spm"])]
        if spm:
            card["foot_spm"] = r0(float(np.median(spm)))
    if bike:
        wd = sum(b["dist"] for b in bike)
        wt = sum(b["dur"] for b in bike)
        card["bike_speed_ms"] = round(wd / wt, 3) if wt else None
        flat = [b for b in bike if ok(b["grade"]) and abs(b["grade"]) <= 0.02]
        if len(flat) >= 4:
            card["bike_flat_speed_ms"] = round(sum(b["dist"] for b in flat) / sum(b["dur"] for b in flat), 3)
            fh = [b["hr"] for b in flat if ok(b["hr"])]
            card["bike_flat_hr"] = r0(float(np.mean(fh))) if fh else None
        steep = [b for b in bike if ok(b["grade"]) and b["grade"] >= 0.05 and _sustained(b, 0.05)]
        if len(steep) >= 4:
            card["bike_climb_vert_mh"] = round(sum(b["dalt"] for b in steep) / sum(b["dur"] for b in steep) * 3600)
            sh = [b["hr"] for b in steep if ok(b["hr"])]
            card["bike_climb_hr"] = r0(float(np.mean(sh))) if sh else None
    if swim:
        sd = sum(b["dist"] for b in swim)
        st = sum(b["dur"] for b in swim)
        card["swim_speed_ms"] = round(sd / st, 3) if st else None
    pw = [b["power"] for b in live if ok(b["power"])]
    if pw:
        card["power_w"] = r0(float(np.mean(pw)))
    tp = [b["temp"] for b in live if ok(b["temp"])]
    if tp:
        card["watch_temp_c"] = r0(float(np.mean(tp)))
    alt = rec["alt"][np.isfinite(rec["alt"])]
    if len(alt):
        card["alt_m"] = [r0(float(alt.min())), r0(float(alt.max()))]
    lat = rec["lat"]
    if np.isfinite(lat).any():
        i = int(np.argmax(np.isfinite(lat)))
        card["start_area"] = [round(float(lat[i]), 2), round(float(rec["lon"][i]), 2)]

    # fastest stretches, measured only where the blocks show that movement
    t = rec["t"]
    for mode, dists in (("foot", ((1, "1mi"), (3, "3mi"))), ("bike", ((10, "10mi"),))):
        mask = np.zeros(len(t), dtype=bool)
        for b in live:
            if b["mode"] == mode:
                mask |= (t >= b["start"]) & (t < b["end"])
        for miles, label in dists:
            v = fastest(rec, miles * 1609.344, mask)
            if v:
                card[f"fastest_{mode}_{label}_s"] = round(v)

    notes = list(act.get("check_notes") or []) + peak_notes
    mov_min, miss_min = hr_coverage(bl)
    if mov_min >= 5 and miss_min / mov_min > 0.2 and miss_min < mov_min:
        notes.append(f"no heart rate for {round(miss_min)} of {round(mov_min)} moving minutes")
    jumps = sum(1 for b in live if b.get("alt_jump"))
    if jumps:
        notes.append(f"{jumps} blocks whose altitude change the rest of the data contradicts (steeper than the "
                     f"ground covered, or a climb rate far outside this person's own) left out "
                     f"of climbing")
    doubt = [b for b in live if b.get("alt_doubt")]
    if doubt:
        notes.append(f"{round(sum(b['dur'] for b in doubt) / 60)} min of climbing faster than this person usually "
                     f"climbs on foot at that heart rate were kept but are doubtful")
    vehicle = sum(1 for b in live if b["mode"] == "fast") if family == "bike" else 0
    if vehicle:
        notes.append(f"{vehicle} blocks far faster than this person ever rides were read as a vehicle, not riding")
    fast = card["minutes"].get("fast", 0)
    moving_min = sum(v for k, v in card["minutes"].items() if k != "still")
    if family == "foot" and moving_min and fast / moving_min > 0.3:
        notes.append(f"recorded as {card['recorded_as']} but {round(fast)} of {round(moving_min)} moving "
                     f"minutes were faster than running")
    if notes:
        card["notes"] = notes
    segs = segments(bl)
    card["structure"] = structure(segs)
    return card, segs, bl


def weekly_rows(cards):
    weeks = defaultdict(lambda: defaultdict(float))
    for c in cards:
        d = date.fromisoformat(c["date"])
        wk = (d - timedelta(days=d.weekday())).isoformat()
        w = weeks[wk]
        w["sessions"] += 1
        for mode, m in c["minutes"].items():
            w[f"{mode}_h"] += m / 60
        w["km"] += c["distance_km"]
        w["up_m"] += c["up_m"]
        for band, m in c["hr_bands_min"].items():
            key = "hr<100" if band < 100 else ("hr180+" if band >= 180 else f"hr{band}s")
            w[key] += m / 60
    if not weeks:
        return []
    d = min(date.fromisoformat(k) for k in weeks)
    last = max(date.fromisoformat(k) for k in weeks)
    rows = []
    while d <= last:
        w = weeks.get(d.isoformat(), {})
        row = {"week_of": d.isoformat()}
        row.update({k: round(v, 1) for k, v in w.items()})
        rows.append(row)
        d += timedelta(days=7)
    return rows


def relationships(cards, blocks_by_id, segs_by_id):
    """Per month: what speed each 10-bpm heart rate band bought.

    foot_flat: speed on foot on grades within 2%; foot_climb: metres climbed
    per hour on foot on grades of 8% or more; bike_flat: speed on a bike on
    grades within 2%. Cells need at least 4 minutes of data."""
    acc = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))  # key -> band -> [num, dur]
    drift = defaultdict(list)
    for c in cards:
        mo = c["date"][:7]
        for b in blocks_by_id.get(c["id"], []):
            if b["pause"] or b["mode"] == "still" or not ok(b["hr"]) or not ok(b["grade"]):
                continue
            band = int(b["hr"] // 10 * 10)
            if b["mode"] == "foot" and abs(b["grade"]) <= 0.02:
                cell = acc[(mo, "foot_flat")][band]
                cell[0] += b["dist"]
                cell[1] += b["dur"]
            elif b["mode"] == "foot" and b["grade"] >= 0.08:
                cell = acc[(mo, "foot_climb")][band]
                cell[0] += b["dalt"]
                cell[1] += b["dur"]
            elif b["mode"] == "bike" and abs(b["grade"]) <= 0.02:
                cell = acc[(mo, "bike_flat")][band]
                cell[0] += b["dist"]
                cell[1] += b["dur"]
        for s in segs_by_id.get(c["id"], []):
            if s["mode"] in ("foot", "bike") and s["dur_s"] >= 900 and s.get("hr_first") and s.get("hr_last") \
                    and abs(s.get("grade_pct") or 0) <= 3:
                drift[(mo, s["mode"])].append((s["hr_last"] - s["hr_first"]) / (s["dur_s"] / 600))
    out = defaultdict(dict)
    for (mo, kind), bands in acc.items():
        row = {}
        for band, (num, dur) in sorted(bands.items()):
            if dur < 240:
                continue
            if kind == "foot_climb":
                row[band] = round(num / dur * 3600)
            else:
                row[band] = round(num / dur, 3)
        if row:
            out[kind][mo] = row
    drift_out = defaultdict(dict)
    for (mo, mode), vals in drift.items():
        drift_out[mode][mo] = {"bpm_per_10min": round(statistics.median(vals), 1), "n": len(vals)}
    return {"by_kind": {k: dict(sorted(v.items())) for k, v in out.items()},
            "steady_drift": {k: dict(sorted(v.items())) for k, v in drift_out.items()}}


def hr_envelope(cards):
    """Highest and lowest held heart rates. A top value more than 8 bpm above
    every other session's is set aside as a likely sensor error, not a maximum."""
    peaks = sorted((c for c in cards if c.get("hr_peak_1min")), key=lambda c: -c["hr_peak_1min"])
    suspect = []
    while len(peaks) > 1 and peaks[0]["hr_peak_1min"] - peaks[1]["hr_peak_1min"] > 8:
        suspect.append(peaks.pop(0))
    peaks = peaks[:6]
    lows = sorted((c for c in cards if c.get("hr_low_5min")), key=lambda c: c["hr_low_5min"])[:4]
    return {"highest_1min": [[c["hr_peak_1min"], c["date"], c["id"]] for c in peaks],
            "set_aside_1min": [[c["hr_peak_1min"], c["date"], c["id"]] for c in suspect],
            "lowest_5min": [[c["hr_low_5min"], c["date"], c["id"]] for c in lows]}


# ------------------------------------------------------------------ building

BLOCK_KEYS = ("pause", "start", "end", "dur", "mode", "hr", "grade", "dist", "dalt", "alt_jump", "alt_doubt")


def slim_blocks(bl):
    """What the weekly and heart-rate tables need from each 30-second block."""
    out = []
    for b in bl:
        row = {}
        for k in BLOCK_KEYS:
            v = b.get(k)
            if isinstance(v, float) and math.isnan(v):
                v = None
            row[k] = round(v, 3) if isinstance(v, float) else v
        out.append(row)
    return out


def _unslim(bl):
    return [{k: (math.nan if v is None and k in ("hr", "grade", "dalt") else v) for k, v in b.items()} for b in bl]


def session_rows(acts, tz, caps=None, source="export"):
    """Cards, segments and slimmed blocks for a list of sessions. Heart-rate
    readings that fail the consistency checks are left out first (checks.py).
    Without caps, this person's own limits are computed from these sessions."""
    all_bl = []
    for a in acts:
        a["family"] = FAMILY.get(a["sport"], "foot")
        a["check_notes"] = check_record(a["rec"], a["family"])
        all_bl.append(blocks(a["rec"], a["family"]))
    if caps is None:
        caps = person_caps(all_bl)
    rows = []
    for a, bl in zip(acts, all_bl):
        mark_jumps(bl, caps)
        card, segs, bl = session_card(a, tz, bl)
        if not card:
            continue
        rows.append({"id": card["id"], "date": card["date"], "source": source, "card": card,
                     "segments": segs, "blocks": slim_blocks(bl)})
    return rows, caps


def apply_fences(cards):
    """Remove card values far outside this person's own curve, naming each in
    the card's notes. Works on copies; returns the computed fences."""
    limits = {}
    for key, side in CHECKED.items():
        fence = far_out([c[key] for c in cards if c.get(key)], side)
        limits[key] = {"side": side, "fence": round(fence, 3) if fence else None}
        if not fence:
            continue
        for c in cards:
            v = c.get(key)
            if v is not None and (v > fence if side == "hi" else v < fence):
                c.setdefault("notes", []).append(
                    f"{key} {v} is far outside this person's own range (fence {round(fence, 2)}) and was removed")
                del c[key]
    return limits


def assemble(rows, caps, night_rows, today, built, person="athlete", extra_lines=None):
    """The whole record as text, from stored and fresh session rows."""
    from .render import render
    meta, env, rel, weekly, cards, seg_rows = prepare(rows, caps, night_rows, built, person)
    text = render(meta, env, rel, weekly, cards, seg_rows, today, night_rows)
    if extra_lines:
        text += "\n" + "\n".join(extra_lines) + "\n"
    return text, meta


def prepare(rows, caps, night_rows, built, person="athlete"):
    """Everything the record is written from: (meta, env, rel, weekly, cards, seg_rows)."""
    rows = sorted(rows, key=lambda r: r["id"])
    cards = [dict(r["card"]) for r in rows]
    for c in cards:
        # JSON keeps dictionary keys as text; the heart-rate bands are numbers
        c["hr_bands_min"] = {int(k): v for k, v in (c.get("hr_bands_min") or {}).items()}
        c["notes"] = [n for n in c.get("notes", []) if "far outside this person's own range (fence" not in n]
        if not c["notes"]:
            del c["notes"]
    limits = {"altitude_jump_caps_m_per_h": {k: (round(v) if v else None) for k, v in (caps or {}).items()}}
    limits.update(apply_fences(cards))
    prev = None
    for c in cards:
        d = date.fromisoformat(c["date"])
        c["days_since_prev"] = (d - prev).days if prev else None
        prev = d
    blocks_by_id = {r["id"]: _unslim(r["blocks"]) for r in rows}
    segs_by_id = {r["id"]: r["segments"] for r in rows}
    seg_rows = [{"id": r["id"], "date": r["date"], "segments": r["segments"]} for r in rows]
    weekly = weekly_rows(cards)
    rel = relationships(cards, blocks_by_id, segs_by_id)
    env = hr_envelope(cards)
    meta = {"person": person, "built": built, "sessions": len(cards),
            "history": [cards[0]["date"], cards[-1]["date"]] if cards else ["none", "none"],
            "nights": len(night_rows), "limits": limits}
    for c, r in zip(cards, rows):
        c["id"] = r["id"]
    return meta, env, rel, weekly, cards, seg_rows
