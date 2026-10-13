"""Render a person's catalogue as the text that goes into the daily prompt.

Every session gets its own line; the most recent two weeks get every segment.
"""
from __future__ import annotations

from datetime import timedelta

from .raw import dur_str, pace_str, seg_text

RECENT_DAYS = 14


def _pace(ms):
    p = pace_str(ms)
    return f"{p}/km" if p else None


def card_line(c):
    m = c["minutes"]
    mins = ", ".join(f"{k} {dur_str(v * 60)}" for k, v in sorted(m.items(), key=lambda kv: -kv[1]) if v >= 0.5)
    bits = [f"{c['date']} {c['weekday']} {c['start']}", f"recorded as {c.get('recorded_as', '?')}",
            f"span {dur_str(c['span_min'] * 60)}"]
    if c["paused_min"] >= 1:
        bits.append(f"paused {dur_str(c['paused_min'] * 60)}")
    bits.append(mins)
    if c["distance_km"] >= 0.1:
        bits.append(f"{c['distance_km']} km")
    if c["up_m"] or c["down_m"]:
        bits.append(f"+{c['up_m']}/-{c['down_m']} m")
    if c.get("foot_speed_ms"):
        bits.append(f"foot {_pace(c['foot_speed_ms'])}")
    if c.get("foot_flat_speed_ms"):
        bits.append(f"flat {_pace(c['foot_flat_speed_ms'])} at HR {c.get('foot_flat_hr')}")
    if c.get("climb_vert_mh"):
        bits.append(f"steep climbing {c['climb_vert_mh']} m/h at HR {c.get('climb_hr')}")
    if c.get("bike_speed_ms"):
        bits.append(f"bike {c['bike_speed_ms'] * 3.6:.1f} km/h")
    if c.get("bike_flat_speed_ms"):
        bits.append(f"bike flat {c['bike_flat_speed_ms'] * 3.6:.1f} km/h at HR {c.get('bike_flat_hr')}")
    if c.get("bike_climb_vert_mh"):
        bits.append(f"bike climbing {c['bike_climb_vert_mh']} m/h at HR {c.get('bike_climb_hr')}")
    if c.get("swim_speed_ms"):
        sec = int(round(100 / c["swim_speed_ms"]))
        bits.append(f"swim {sec // 60}:{sec % 60:02d}/100m")
    if c.get("hr_moving"):
        bits.append(f"HR moving {c['hr_moving']}")
    if c.get("hr_peak_1min"):
        bits.append(f"HR 1-min peak {c['hr_peak_1min']}, low {c['hr_low_1min']}")
    bands = c.get("hr_bands_min") or {}
    if bands:
        bits.append("HR min by band " + " ".join(f"{k}:{round(v)}" for k, v in bands.items() if v >= 0.5))
    if c.get("foot_spm"):
        bits.append(f"{c['foot_spm']} spm")
    if c.get("power_w"):
        bits.append(f"{c['power_w']} W")
    for key, label in (("fastest_foot_1k_s", "fastest foot 1k"), ("fastest_foot_5k_s", "fastest foot 5k"),
                       ("fastest_bike_10k_s", "fastest bike 10k")):
        if c.get(key):
            bits.append(f"{label} {dur_str(c[key])}")
    if c.get("alt_m"):
        bits.append(f"alt {c['alt_m'][0]}-{c['alt_m'][1]} m")
    if c.get("watch_temp_c") is not None:
        bits.append(f"watch {c['watch_temp_c']} C")
    if c.get("days_since_prev") is not None:
        bits.append(f"{c['days_since_prev']}d after previous")
    if c.get("notes"):
        bits.append("NOTE: " + "; ".join(c["notes"]))
    line = " | ".join(bits)
    if c.get("structure"):
        line += f"\n    {c['structure']}"
    return line


def render(meta, env, rel, weekly, cards, seg_rows, today, night_rows=()):
    L = []
    L.append(f"# Training catalogue: {meta['person']}")
    L.append(f"Built {meta['built']}. {meta['sessions']} recorded sessions, "
             f"{meta['history'][0]} to {meta['history'][1]}; {meta.get('nights', 0)} nights with overnight data.")
    L.append("Everything below is computed from the watch's second-by-second records only: time, "
             "heart rate, distance, altitude, cadence, power, temperature and position. None of "
             "Garmin's labels, settings or estimates are used: no activity type, no zones, no "
             "training effect, no max heart rate setting. The one label used is the sport chosen on "
             "the watch ('recorded as'), and only to tell rides and swims from movement on foot. "
             "Movement is classified in 30-second blocks: 'bike' or 'swim' (moving, in a session "
             "recorded as cycling or swimming), 'foot' (moving, any other session), 'fast' (moving "
             "faster than 6 m/s in a session not recorded as cycling: a vehicle, lift or ski run), "
             "'still' (under 10 m per block), 'paused' (no records). Climb and descent count "
             "altitude changes of 3 m or more within unbroken recording. Plausibility is judged only "
             "against this person's own data: a value far outside their own spread (beyond the "
             "quartile plus three interquartile ranges, on a log scale) is removed and named in a "
             "NOTE, and blocks whose two-minute climb rate is far outside their own spread are left "
             "out of climbing. 'HR min by band' "
             "is minutes in each 10-bpm band (110 means 110-119). Watch temperature reads high "
             "because the watch sits on the wrist.")
    lim = meta.get("limits")
    if lim:
        caps = lim.get("altitude_jump_caps_m_per_h", {})
        L.append("This person's own limits: altitude jumps above " + ", ".join(
            f"{k.replace('_', ' ')} {v} m/h" for k, v in caps.items() if v) + "; card values beyond " + ", ".join(
            f"{k} {v['fence']}" for k, v in lim.items() if isinstance(v, dict) and v.get("fence")) + ".")
    L.append("")

    L.append("## Heart rate seen in the data")
    L.append("Highest heart rate held for one minute: " + "; ".join(
        f"{v} ({d})" for v, d, _ in env["highest_1min"]))
    if env.get("set_aside_1min"):
        L.append("Set aside as likely sensor errors (more than 8 bpm above every other session): " + "; ".join(
            f"{v} ({d})" for v, d, _ in env["set_aside_1min"]))
    L.append("Lowest heart rate held for five minutes during a session: " + "; ".join(
        f"{v} ({d})" for v, d, _ in env["lowest_5min"]))
    L.append("")

    L.append("## What each heart rate bought, by month")
    labels = {"foot_flat": "On foot, flat (grade within 2%): pace in each 10-bpm band",
              "foot_climb": "On foot, steep (grade 8% or more): metres climbed per hour in each band",
              "bike_flat": "On a bike, flat: km/h in each band"}
    for kind, months in rel["by_kind"].items():
        L.append(f"### {labels.get(kind, kind)}")
        for mo, row in months.items():
            if kind == "foot_flat":
                cells = [f"{b}: {pace_str(v)}" for b, v in row.items()]
            elif kind == "bike_flat":
                cells = [f"{b}: {v * 3.6:.1f}" for b, v in row.items()]
            else:
                cells = [f"{b}: {v}" for b, v in row.items()]
            L.append(f"- {mo}: " + "; ".join(cells))
    if rel["steady_drift"]:
        L.append("### Heart-rate drift in steady stretches of 15 minutes or more on level ground "
                 "(bpm rise per 10 minutes, monthly median)")
        for mode, months in rel["steady_drift"].items():
            L.append(f"- {mode}: " + "; ".join(f"{mo} {v['bpm_per_10min']:+} (n={v['n']})"
                                              for mo, v in months.items()))
    L.append("")

    L.append("## Weeks (Monday date: sessions; hours by movement; km; climb; hours by 10-bpm band)")
    for w in weekly:
        if len(w) == 1:
            L.append(f"- {w['week_of']}: none")
            continue
        modes = ", ".join(f"{k[:-2]} {w[k]}h" for k in ("foot_h", "bike_h", "swim_h", "fast_h", "still_h") if w.get(k))
        bands = " ".join(f"{k[2:]}:{w[k]}" for k in w if k.startswith("hr") and w[k] >= 0.1)
        L.append(f"- {w['week_of']}: {int(w.get('sessions', 0))} sessions; {modes}; "
                 f"{w.get('km', 0)} km; +{int(w.get('up_m', 0))} m; HR h {bands}")
    L.append("")

    L.append("## Every session, oldest first (second line: segments where the data changed; "
             "'repeats' marks three or more similar efforts with easier stretches between)")
    for c in cards:
        L.append(card_line(c))
    L.append("")

    if night_rows:
        L.append("## Nights, from the raw overnight measurements")
        L.append("One line per night, dated by the morning it ended. The window is the time the watch "
                 "recorded sleep starting and ending; where there was no sleep record it is 00:00 to 06:00 "
                 "and the line says 'fixed'. HR comes from the watch's all-day heart rate (about one "
                 "reading a minute): mean over the window, lowest 30-minute mean, lowest single reading. "
                 "HRV is the watch's overnight readings, about one every five minutes, in ms: mean, "
                 "median, lowest and highest. Breaths is breaths per minute. Sleep stages, sleep scores, "
                 "HRV status and Garmin's resting heart rate are left out. Nights the watch was not worn "
                 "do not appear.")
        for n in night_rows:
            bits = [f"{n['night']} {n['weekday']}"]
            if n["window"] == "sleep":
                bits.append(f"{n['start']}-{n['end']} ({n['hours']} h)")
            else:
                bits.append("fixed 00-06")
            if n["hr_mean"]:
                bits.append(f"HR mean {n['hr_mean']}, low 30-min {n['hr_low_30min']}, min {n['hr_min']} "
                            f"({n['hr_readings']} readings)")
            if n["hrv_readings"]:
                bits.append(f"HRV mean {n['hrv_mean']}, median {n['hrv_median']}, "
                            f"{n['hrv_low']}-{n['hrv_high']} ({n['hrv_readings']} readings)")
            if n["breaths_mean"]:
                bits.append(f"breaths {n['breaths_mean']}")
            L.append(" | ".join(bits))
        L.append("")

    cut = (today - timedelta(days=RECENT_DAYS)).isoformat()
    recent = [s for s in seg_rows if s["date"] >= cut]
    L.append(f"## Last {RECENT_DAYS} days, every segment")
    if not recent:
        L.append("No sessions in this window.")
    for s in recent:
        L.append(f"### {s['date']} {s['id']}")
        for g in s["segments"]:
            extra = []
            if g.get("dist_m"):
                extra.append(f"{g['dist_m']} m")
            if g.get("up_m") or g.get("down_m"):
                extra.append(f"+{g.get('up_m', 0)}/-{g.get('down_m', 0)} m")
            if g.get("spm"):
                extra.append(f"{g['spm']} spm")
            if g.get("power"):
                extra.append(f"{g['power']} W")
            L.append(f"- at {dur_str(g['start_s'])}: {seg_text(g)}" + (f"; {', '.join(extra)}" if extra else ""))
    L.append("")
    return "\n".join(L)
