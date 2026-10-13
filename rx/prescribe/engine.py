"""The prescription engine. Deterministic: same data in, same prescription out.

Five parts, always: Verdict, Why, Do, Don't, Tonight. Plus if/then scenarios.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from ..analysis import load as la
from ..analysis import sleep as sleepbank
from ..analysis.reading import Reading, cap_verdict, read
from ..analysis.recovery import AMBER, GREEN, RED, Recovery, assess
from ..profile import Profile
from ..sports import BIKE, LABEL, RUN, SKI, WALK

REST, LIGHT, EASY, STEADY, HARD = "rest", "light", "easy", "steady", "hard"
ORDER = [REST, LIGHT, EASY, STEADY, HARD]
HEADLINE = {
    REST: "Day off.",
    LIGHT: "Just a stroll.",
    EASY: "Easy day.",
    STEADY: "Go. Steady.",
    HARD: "Go hard.",
}
TOMORROW = {
    REST: "a rest day",
    LIGHT: "a walk-only day",
    EASY: "an easy day",
    STEADY: "a steady day with no intervals",
    HARD: "a green light for long or hard",
}
WALK_MPH = 3.3
EARLY_BED = "In bed by 9:30 PM."


@dataclass
class Rx:
    date: str
    verdict: str
    headline: str
    why: list[str]
    do: str
    dont: str
    tonight: str
    sport: str
    hr_low: int | None
    hr_ceiling: int | None
    max_minutes: int | None
    steps: list[str] = field(default_factory=list)
    compress: str | None = None
    stretch: str | None = None
    if_skip: str | None = None
    if_overdo: str | None = None
    scenarios: list[str] = field(default_factory=list)
    recovery: Recovery | None = None
    notes: list[str] = field(default_factory=list)
    reading: Reading | None = None
    skip_short: str = ""
    example: str = ""
    minimum: str = ""
    options: list = field(default_factory=list)     # other ways to do today: (title, note, tone)
    options_short: str = ""
    tomorrow_short: str = ""
    why_need: str = ""
    why_story: str = ""
    how_short: str = ""
    how_more: list = field(default_factory=list)
    tomorrow: list = field(default_factory=list)   # (label, tone, text): as written / overdo / skip
    why_title: str = ""
    why_tone: str = "none"
    why_says: str = ""
    diagnosis: str = ""
    activity_why: str = ""
    what: str = ""
    how: list = field(default_factory=list)
    sleep: object = None
    sleep_if: list = field(default_factory=list)
    overdo_short: str = ""


@dataclass
class Inputs:
    """Everything the decision depends on; separated so tests and scenarios can build it directly."""
    recovery: Recovery
    yesterday: la.LoadDay | None
    last_two: list[float]


def decide(i: Inputs) -> tuple[str, list[str]]:
    """Return (verdict, reasons). The reasons are the 'Why' line, strongest first."""
    rec, y = i.recovery, i.yesterday
    reasons: list[str] = []
    ctl = y.ctl if y else 0.0
    ratio = y.ratio if y else 1.0
    big_yday = bool(y and ctl > 5 and y.load > 1.5 * ctl)
    hard_recent = any(v > 1.8 * ctl for v in i.last_two) if ctl > 5 else False

    for s in rec.signals:
        if s.status in (RED, AMBER):
            reasons.append(f"{s.name}: {s.detail}")
    if ctl > 5 and abs(ratio - 1) >= 0.15:
        pct = round((ratio - 1) * 100)
        reasons.append(f"Last 7 days of load are {abs(pct)}% {'above' if pct > 0 else 'below'} your 6-week norm.")
    if big_yday:
        reasons.append("Yesterday was a big day.")

    if rec.reds >= 2:
        v = REST
    elif rec.reds == 1 or ratio > 1.5:
        v = LIGHT
    elif rec.ambers >= 2 or ratio > 1.3:
        v = EASY
    elif rec.known == 0:
        v = EASY
        reasons.append("No sleep or HRV data for last night, so today stays conservative.")
    elif rec.ambers == 1:
        v = STEADY
    elif big_yday or hard_recent or ratio > 1.15:
        v = STEADY
        reasons.insert(0, "Recovery looks good: you're absorbing the recent work.")
    else:
        v = HARD
        reasons.insert(0, "Recovered, and nothing hard in the last two days.")
    if not reasons:
        reasons.append("Sleep, HRV and resting HR are all in your normal range.")
    return v, reasons


def _pick_sport(verdict: str, p: Profile) -> str:
    if verdict in (REST, LIGHT):
        return WALK
    for s in p.sport_mix:
        if s in (BIKE, RUN, SKI):
            return s
    return BIKE


def _minutes(verdict: str, sport: str, p: Profile) -> int:
    typical = p.typical_minutes.get(sport, 75)
    if verdict == REST:
        return 40
    if verdict == LIGHT:
        return 60
    if verdict == EASY:
        return int(min(75, max(45, typical * 0.8)))
    if verdict == STEADY:
        return int(min(150, max(60, typical)))
    return 75


def _round5(m: float) -> int:
    return int(5 * round(m / 5))


def build_rx(p: Profile, day: str | None = None) -> Rx:
    day = day or p.as_of
    rec = assess(p.wellness, day)
    y = p.yesterday
    last_two = [ld.load for ld in p.load_days[-2:]]
    verdict, why = decide(Inputs(rec, y, last_two))
    reading = read(p.wellness, p.daily_load, p.load_days, day, p.response)
    verdict = cap_verdict(verdict, reading.cap)
    why = [reading.says] + [r for r in why if not r.startswith(
        ("HRV", "Resting HR", "Sleep", "Recovered", "Recovery looks", "No sleep"))]
    sb = sleepbank.bank(p.wellness, p.daily_load, day)
    before = verdict
    verdict = cap_verdict(verdict, sb.cap)
    if sb.state in ("owed", "debt", "deep_debt") or verdict != before or sb.no_technical:
        why.insert(1, sb.says)
    sport, chosen_kind, sport_reason, cap_mins = choose_activity(verdict, p, day)
    mins = _round5(_minutes(verdict, sport, p) * (sb.volume if verdict not in (REST, LIGHT) else 1.0))
    if cap_mins:
        mins = min(mins, cap_mins)
    diag = diagnosis(verdict, p, day, reading, sb)

    if not p.ready:
        return Rx(day, verdict, HEADLINE[verdict], why,
                  do=f"{LABEL[sport]}, {mins} min max, conversational effort.",
                  dont="No hard efforts until your heart-rate profile is built.",
                  tonight=EARLY_BED if rec.sleep.status in (RED, AMBER) else "Normal bedtime.",
                  sport=sport, hr_low=None, hr_ceiling=None, max_minutes=mins, recovery=rec,
                  notes=["Not enough hard efforts yet to set your heart-rate ranges. "
                         "Sync more history or upload the Garmin export."])

    z = p.zones
    steps: list[str] = []
    kind = chosen_kind or usual_kind(p, sport)
    verb = {"mountain bike": "Mountain bike", "road ride": "Road ride", "gravel ride": "Gravel ride",
            "trail run": "Trail run", "ski tour": "Skin"}.get(kind or "", None) or \
        {BIKE: "Ride", RUN: "Run", SKI: "Skin", WALK: "Walk"}.get(sport, LABEL[sport])
    if verdict in (REST, LIGHT):
        ceiling = z.top("Z1 Recovery")
        low = None
        if verdict == LIGHT:
            do = (f"No workout today. The most you should do is a relaxed walk, up to {mins} minutes, "
                  f"heart rate under {ceiling}. A stroll, not a power walk.")
            dont = "Don't sneak in a ride or a run. Don't speed-walk to rack up steps, and skip the steep hills."
        else:
            do = (f"Take the day off. If you want to move, a slow walk of {mins} minutes tops keeps you loose. "
                  f"Heart rate under {ceiling}.")
            dont = "No training at all today. Resting is the workout."
    elif verdict == EASY:
        low, ceiling = easy_band(z)
        do = (f"{verb} easy, {mins} minutes max, heart rate {low}–{ceiling}. Easy enough to take a phone call the whole way without the other person hearing you breathe.")
        dont = f"Don't let the climbs drag you over {ceiling}. Shift down, spin, or walk them."
    elif verdict == STEADY:
        low, ceiling = z.bottom("Z2 Aerobic"), z.top("Z2 Aerobic")
        avg = round((low + ceiling) / 2)
        do = (f"{verb} up to {mins} minutes at all-day pace: heart rate {low}–{ceiling}, settling around {avg}. "
              f"You can talk, just not sing.")
        dont = f"No chasing, no attacking climbs, no intervals. Nothing over {ceiling}."
    else:  # HARD
        z4lo, z4hi = z.bottom("Z4 Threshold"), z.top("Z4 Threshold")
        low, ceiling = z4lo, z4hi + 3
        steps = [
            f"15 min easy to warm up, heart rate under {z.top('Z2 Aerobic')}.",
            f"4 × 8 min hard at {z4lo}–{z4hi}, with 4 min easy between. Hard but even: a few words at a time.",
            f"10 min easy to finish.",
        ]
        do = f"{verb} with 4 hard 8-minute efforts at heart rate {z4lo}–{z4hi}. 75 minutes total."
        dont = f"Don't go over {ceiling}, even on the last one. Don't add a fifth."

    if sport in (BIKE, SKI) and verdict in (EASY, STEADY):
        # Mountain riding and skinning are intermittent: climbs spike HR, descents drop it.
        # The ceiling is about what you sustain, not a single beep on a punchy pitch.
        dont += (f" A spike on a steep pitch is fine if it's under 30 seconds. "
                 f"Over {ceiling} for a full minute means back off.")
    tonight = EARLY_BED if (rec.sleep.status in (RED, AMBER) or verdict in (REST, LIGHT, HARD)) \
        else "Normal bedtime. Aim for 8 hours in bed."
    rx = Rx(day, verdict, HEADLINE[verdict], why[:3], do, dont, tonight, sport,
            low, ceiling, mins, steps, recovery=rec, reading=reading)
    sb = sleepbank.bank(p.wellness, p.daily_load, day, verdict)   # tonight's need depends on today's session
    rx.sleep = sb
    rx.tonight = (f"In bed by {sb.lights_out}, up at {sb.wake}. That gets you the "
                  f"{sleepbank._hm(sb.need_tonight)} of sleep you need tonight: " + ", ".join(sb.parts) + ".")
    if sb.no_technical and sport in (BIKE, SKI):
        rx.dont += " No technical descents today: under 5 hours of sleep, reaction time goes first."
    need = round(sb.need_tonight * 4) / 4
    for label, hrs in ((f"Get your {sleepbank._hm(need)}", need), ("Sleep 6 hours", 6.0), ("Sleep 5 hours", 5.0)):
        cap = sleepbank.tomorrow_if(p.wellness, p.daily_load, day, hrs)
        rx.sleep_if.append((label, cap, {HARD: "sleep won't hold you back tomorrow",
                                         STEADY: "tomorrow caps at steady: no intervals",
                                         EASY: "tomorrow is easy only",
                                         LIGHT: "tomorrow is a stroll at most",
                                         REST: "tomorrow is a day off"}[cap]))
    p._today_low = low
    rx.example = example_ride(p, sport, verdict, mins, ceiling, kind)
    rx.diagnosis, rx.activity_why = diag, sport_reason
    rx.why_title, rx.why_tone, rx.why_says = why_lead(verdict, p, day, reading)
    rx.why_need, rx.why_story = why_story(verdict, p, day, sb, reading)
    rx.what, rx.how = what_how(rx, kind, mins, low, ceiling, steps)
    rx.minimum = minimum_dose(p, rx, kind)
    rx.options = alternatives(p, rx, kind)
    if rx.options:
        names = [o[3] for o in rx.options[:3]]
        rx.options_short = "Or " + (names[0] if len(names) == 1 else ", ".join(names[:-1]) + " or " + names[-1]) + \
            ", same heart rate."
    key = next((i for i, h in enumerate(rx.how) if "×" in h), 0) if verdict == HARD else 0
    rx.how_short = rx.how[key] if rx.how else rx.do
    rx.how_more = [h for i, h in enumerate(rx.how) if i != key]
    rx.scenarios = scenarios(p, rx)
    rx.if_skip, rx.if_overdo = consequences(p, rx)
    rx.tomorrow = tomorrow_plan(p, rx, kind)
    rx.compress, rx.stretch = flex(p, rx)
    if not p.max_hr.confirmed and p.max_hr.bpm:
        rx.notes.append(f"Max HR {p.max_hr.bpm} is from a single day; ranges firm up as it's matched.")
    return rx


def _est_load(p: Profile, minutes: float, avg_hr: float) -> float:
    return la.trimp_from_summary(minutes, avg_hr, p.rest_hr, p.max_bpm)


def scenarios(p: Profile, rx: Rx) -> list[str]:
    """If you do X today, then tomorrow (assuming a normal night) is Y."""
    if not p.ready or not p.yesterday:
        return []
    z = p.zones
    options = [
        ("walk", f"just walk today", _est_load(p, 45, z.top("Z1 Recovery") - 15)),
        ("rx", "follow today's prescription",
         _est_load(p, rx.max_minutes or 60, ((rx.hr_low or rx.hr_ceiling - 15) + rx.hr_ceiling) / 2)),
        ("big", f"go long and hard instead (3 hours)", _est_load(p, 180, z.bottom("Z3 Tempo") + 2)),
    ]
    good_night = Recovery(rx.date, *[_assume_green(s) for s in rx.recovery.signals])
    tomorrow = (date.fromisoformat(rx.date) + timedelta(days=1)).isoformat()
    out, seen = [], set()
    for key, label, load in options:
        if key == "walk" and rx.verdict in (REST, LIGHT):
            continue
        y2 = la.step(p.yesterday, load, rx.date)
        v, _ = decide(Inputs(good_night, y2, [p.yesterday.load, load]))
        if (key, v) in seen:
            continue
        seen.add((key, v))
        out.append(f"If you {label}, tomorrow is {TOMORROW[v]}, provided you sleep well.")
    return out


def _rx_load(p: Profile, rx: Rx) -> float:
    if rx.verdict == HARD:
        z = p.zones
        return (_est_load(p, 32, (z.bottom("Z4 Threshold") + z.top("Z4 Threshold")) / 2)
                + _est_load(p, 43, z.top("Z1 Recovery")))
    mid = ((rx.hr_low if rx.hr_low is not None else rx.hr_ceiling - 15) + rx.hr_ceiling) / 2
    return _est_load(p, rx.max_minutes or 60, mid)


def _next_verdict(p: Profile, rx: Rx, load: float) -> str:
    good_night = Recovery(rx.date, *[_assume_green(s) for s in rx.recovery.signals])
    y2 = la.step(p.yesterday, load, rx.date)
    v, _ = decide(Inputs(good_night, y2, [p.yesterday.load, load]))
    return v


def _personal_cost(p: Profile, load: float) -> str:
    r = p.response
    y = p.yesterday
    rel = load / y.ctl if y and y.ctl > 1 else None
    parts = []
    if r.fitted and rel is not None:
        pct = r.hrv_change_pct(rel)
        if pct is not None and pct < -2:
            parts.append(f"A day that size has cost you about {abs(pct):.0f}% of HRV the next morning")
    if r.recovery_days is not None and r.big_days >= 3:
        d = round(r.recovery_days)
        if d == 0:
            parts.append("after your biggest days, HRV has usually been back to normal by the next morning")
        else:
            parts.append(f"after your biggest days, HRV has taken {d + 1} mornings to get back to normal")
    if not parts:
        return "Your personal recovery cost is learned after about 3 weeks of sleep and HRV data."
    s = "; ".join(parts) + "."
    return s[0].upper() + s[1:]


def flex(p: Profile, rx: Rx) -> tuple[str | None, str | None]:
    """Compress: the shortest version that still does the job.
    Stretch: the most you can add before tomorrow gets worse than the ideal leaves it."""
    if not p.ready or not p.yesterday:
        return None, None
    z, ceil = p.zones, rx.hr_ceiling
    if rx.verdict in (REST, LIGHT):
        top = rx.max_minutes + 30
        return (f"15 minutes of walking still counts. Keep HR under {ceil}.",
                f"Walk up to {top} minutes, as long as HR stays under {ceil}. "
                f"The moment it's a workout, it stops being recovery.")
    if rx.verdict == HARD:
        z4lo, z4hi = z.bottom("Z4 Threshold"), z.top("Z4 Threshold")
        three = _est_load(p, 24, (z4lo + z4hi) / 2) + _est_load(p, 36, z.top("Z1 Recovery"))
        five = _est_load(p, 40, (z4lo + z4hi) / 2) + _est_load(p, 50, z.top("Z1 Recovery"))
        ok5 = ORDER.index(_next_verdict(p, rx, five)) >= ORDER.index(_next_verdict(p, rx, _rx_load(p, rx)))
        stretch = (f"A fifth 8-minute rep is fine. Tomorrow stays the same."
                   if ok5 else
                   f"Don't add reps. A fifth one changes tomorrow to {TOMORROW[_next_verdict(p, rx, five)]}.")
        return (f"3 × 8 min at {z4lo}–{z4hi}, 60 minutes total, still gets most of the benefit.",
                stretch)

    mid = ((rx.hr_low or ceil - 15) + ceil) / 2
    ideal_next = ORDER.index(_next_verdict(p, rx, _rx_load(p, rx)))
    most = rx.max_minutes
    for m in range(rx.max_minutes + 15, rx.max_minutes * 2 + 1, 15):
        if ORDER.index(_next_verdict(p, rx, _est_load(p, m, mid))) < ideal_next:
            break
        most = m
    shortest = max(30, _round5(rx.max_minutes * 0.5))
    compress = (f"{shortest} minutes at the same HR {rx.hr_low}–{ceil} still counts. "
                f"Don't make up for it by going harder.")
    if most > rx.max_minutes:
        stretch = (f"You can go to {most} minutes, ceiling unchanged at {ceil}, "
                   f"without changing tomorrow.")
    else:
        stretch = (f"Don't stretch it. Even 15 more minutes changes tomorrow. "
                   f"{rx.max_minutes} minutes is the most today can absorb.")
    return compress, stretch


def consequences(p: Profile, rx: Rx) -> tuple[str | None, str | None]:
    """The price of not following it: skipping it, or doing too much."""
    if not p.ready or not p.yesterday:
        return None, None
    y, z = p.yesterday, p.zones
    rx_load = _rx_load(p, rx)

    # Doing too much: 50% longer, and drifting 8 bpm over the ceiling.
    over_min = int((rx.max_minutes or 60) * 1.5)
    over_load = _est_load(p, over_min, rx.hr_ceiling + 8)
    if rx.verdict in (REST, LIGHT):
        sport = p.sport_mix[0] if p.sport_mix and p.sport_mix[0] != WALK else BIKE
        over_load = _est_load(p, p.typical_minutes.get(sport, 75), z.bottom("Z3 Tempo"))
        lead = f"Train anyway (a normal {LABEL[sport].lower()}) and"
    else:
        lead = f"Go above {rx.hr_ceiling} or past {rx.max_minutes} minutes and"
    nv = _next_verdict(p, rx, over_load)
    ideal_nv = _next_verdict(p, rx, rx_load)
    if ORDER.index(nv) < ORDER.index(ideal_nv):
        effect = f"tomorrow drops from {TOMORROW[ideal_nv]} to {TOMORROW[nv]}, even if you sleep well."
    else:
        effect = f"tomorrow stays {TOMORROW[nv]}, but only if you sleep well."
    if_overdo = f"{lead} {effect} " + _personal_cost(p, over_load)
    if ORDER.index(nv) < ORDER.index(ideal_nv):
        rx.overdo_short = f"Tomorrow becomes {TOMORROW[nv]} instead of {TOMORROW[ideal_nv]}."
    else:
        rx.overdo_short = "You'll need a great night's sleep just to stay on track."
    if rx.verdict in (REST, LIGHT):
        rx.overdo_short = f"You dig the hole deeper. Tomorrow becomes {TOMORROW[nv]}."
    if rx.verdict in (REST, LIGHT):
        if_overdo += " You don't get fitter on a day you can't absorb. You dig the hole deeper."

    if rx.verdict in (REST, LIGHT):
        rx.skip_short = "Nothing lost. But moving helps you recover faster than sitting."
        return "Skipping the walk costs nothing. Moving at all is the point.", if_overdo

    # Skipping: fitness lost today, and if it becomes a habit.
    ctl_rx = la.step(y, rx_load, rx.date).ctl
    ctl_skip = la.step(y, 0.0, rx.date).ctl
    lost_today = (ctl_rx - ctl_skip) / max(ctl_rx, 1) * 100
    ld = y
    for i in range(4):
        ld = la.step(ld, 0.0, rx.date)
    lost_4 = (y.ctl - ld.ctl) / max(y.ctl, 1) * 100
    nv_skip = _next_verdict(p, rx, 0.0)
    rx.skip_short = (f"You lose {lost_today:.1f}% of your fitness today, "
                     f"{lost_4:.0f}% if it turns into 4 days off.")
    if_skip = (f"You give up about {lost_today:.1f}% of your 6-week fitness today, "
               f"and {lost_4:.0f}% if you skip the next 4 days too. "
               f"You'd be fresher tomorrow ({TOMORROW[nv_skip]}), but that's a trade, not a win.")
    return if_skip, if_overdo


def _session(p: Profile, v: str, day: str | None = None, noun: str | None = None) -> tuple[str, float]:
    """A verdict written out as the actual session for you, activity included,
    and roughly what it costs in load."""
    z = p.zones
    if noun:
        return _session_full(p, v, z, noun[:1].upper() + noun[1:])
    noun = "session"
    if day and v not in (REST, LIGHT):
        try:
            sport, kind, _, _ = choose_activity(v, p, day)
            noun = NOUN.get(kind or "", None) or {BIKE: "ride", RUN: "run", SKI: "ski tour", WALK: "walk"}.get(sport, "session")
        except Exception:
            pass
    Noun = noun[:1].upper() + noun[1:]
    txt, load = _session_full(p, v, z, Noun)
    return txt, load


def _session_short(p: Profile, v: str, day: str | None = None) -> str:
    """One line: the kind of day, the activity, the numbers that matter."""
    z = p.zones
    noun = "session"
    if day and v not in (REST, LIGHT):
        try:
            sport, kind, _, _ = choose_activity(v, p, day)
            noun = NOUN.get(kind or "", None) or {BIKE: "ride", RUN: "run", SKI: "ski tour", WALK: "walk"}.get(sport, "session")
        except Exception:
            pass
    if v == HARD:
        return f"a hard day: a {noun} with 4 × 8 minutes at {z.bottom('Z4 Threshold')}–{z.top('Z4 Threshold')}"
    if v == STEADY:
        return f"a steady day: a {noun} up to 90 minutes at {z.bottom('Z2 Aerobic')}–{z.top('Z2 Aerobic')}"
    if v == EASY:
        lo, hi = easy_band(z)
        return f"an easy day: a 60-minute {noun} at {lo}–{hi}"
    return "a walk day" if v == LIGHT else "a day off"


def _session_full(p: Profile, v: str, z, Noun: str) -> tuple[str, float]:
    if v == HARD:
        lo, hi = z.bottom("Z4 Threshold"), z.top("Z4 Threshold")
        return (f"a hard day. {Noun}, 75 minutes on a steady climb or flat road: 15 easy under "
                f"{z.top('Z2 Aerobic')}, then 4 × 8 minutes at {lo}–{hi} with 4 easy between, 10 easy to finish",
                _est_load(p, 32, (lo + hi) / 2) + _est_load(p, 43, z.top("Z1 Recovery")))
    if v == STEADY:
        lo, hi = z.bottom("Z2 Aerobic"), z.top("Z2 Aerobic")
        return (f"a steady day. {Noun}, up to 90 minutes at {lo}–{hi}: you can talk, not sing. No intervals",
                _est_load(p, 90, (lo + hi) / 2))
    if v == EASY:
        lo, hi = easy_band(z)
        return (f"an easy day. {Noun}, 60 minutes at {lo}–{hi}",
                _est_load(p, 60, (lo + hi) / 2))
    if v == LIGHT:
        return "a walk day. 30 to 45 minutes, nothing more", _est_load(p, 45, z.top("Z1 Recovery") - 15)
    return "a day off", 0.0


def tomorrow_plan(p: Profile, rx: Rx, kind: str | None = None) -> list:
    """Tomorrow three ways: do today as written, overdo it, skip it.
    Each path is projected two days out, so the trade is visible: what you gain today
    against what it costs tomorrow. Assumes a normal night; re-read tomorrow morning."""
    if not p.ready or not p.yesterday or not p.zones:
        return []
    y, nxt = p.yesterday, (date.fromisoformat(rx.date) + timedelta(days=1)).isoformat()
    rx_load = _rx_load(p, rx)
    if rx.verdict in (REST, LIGHT):
        sport = p.sport_mix[0] if p.sport_mix and p.sport_mix[0] != WALK else BIKE
        over_load = _est_load(p, p.typical_minutes.get(sport, 75), p.zones.bottom("Z3 Tempo"))
        over_what = f"Train anyway (a normal {LABEL[sport].lower()})."
    else:
        over_min = int((rx.max_minutes or 60) * 1.5)
        over_load = _est_load(p, over_min, rx.hr_ceiling + 8)
        over_what = f"Go {over_min} minutes or drift over {rx.hr_ceiling}."

    def path(today_load):
        v = _next_verdict(p, rx, today_load)
        txt, tl = _session(p, v, nxt)
        d1 = la.step(y, today_load, rx.date)
        d2 = la.step(d1, tl, nxt)
        return v, txt, d2

    v_rx, t_rx, d_rx = path(rx_load)
    v_ov, t_ov, d_ov = path(over_load)
    out = [("Do it as written", "good", f"Tomorrow is {t_rx}.")]
    rx.tomorrow_short = f"Do today as written and tomorrow is {_session_short(p, v_rx, nxt)}."

    # The minimum: what it does to tomorrow.
    if rx.minimum and rx.verdict not in (REST, LIGHT):
        z = p.zones
        if rx.verdict == HARD:
            lo, hi = z.bottom("Z4 Threshold"), z.top("Z4 Threshold")
            min_load = _est_load(p, 20, (lo + hi) / 2) + _est_load(p, 20, z.top("Z1 Recovery"))
        else:
            floor = 30 if (rx.max_minutes or 0) >= 60 else 20
            min_load = _est_load(p, floor, ((rx.hr_low or rx.hr_ceiling - 15) + rx.hr_ceiling) / 2)
        v_min, t_min, _ = path(min_load)
        if v_min == v_rx:
            rx.minimum += " Tomorrow doesn't change."
        elif ORDER.index(v_min) > ORDER.index(v_rx):
            rx.minimum += f" Tomorrow opens up to {t_min.split('.')[0]}, since you'll be fresher."
        else:
            rx.minimum += f" Tomorrow becomes {t_min.split('.')[0]}."

    # Overdo: what tomorrow becomes, what your body does, and the two-day trade.
    parts = []
    if ORDER.index(v_ov) < ORDER.index(v_rx):
        parts.append(f"Tomorrow drops to {t_ov}.")
    else:
        parts.append(f"Tomorrow can still be {t_ov.split('.')[0]}, but only after a full night's sleep.")
    r, rel = p.response, (over_load / y.ctl if y.ctl > 1 else None)
    pct = r.hrv_change_pct(rel) if (r and r.fitted and rel is not None) else None
    if pct is not None and pct < -2:
        parts.append(f"A day that size has cost you about {abs(pct):.0f}% of HRV the next morning.")
    fit = (d_ov.ctl - d_rx.ctl) / max(d_rx.ctl, 1) * 100
    tired = (d_ov.atl - d_rx.atl) / max(d_rx.atl, 1) * 100
    dropped = ORDER.index(v_ov) < ORDER.index(v_rx)
    if fit < 0.5:
        parts.append(f"Two days out you're no fitter, just {tired:.0f}% more tired.")
    elif dropped:
        parts.append(f"Two days out you carry {tired:.0f}% more fatigue for {fit:.0f}% more fitness, "
                     f"and you've lost tomorrow's session to get it.")
    else:
        parts.append(f"Two days out you carry {tired:.0f}% more fatigue for {fit:.0f}% more fitness.")
    out.append(("Overdo it", "bad", f"{over_what} " + " ".join(parts)))

    # Skip.
    if rx.verdict in (REST, LIGHT):
        out.append(("Skip it", "muted", f"Tomorrow is still {t_rx.split('.')[0]}. Skipping a light day costs nothing."))
    else:
        v_sk, t_sk, d_sk = path(0.0)
        lost = (d_rx.ctl - d_sk.ctl) / max(d_rx.ctl, 1) * 100
        same = ORDER.index(v_sk) <= ORDER.index(v_rx)
        out.append(("Skip it", "muted",
                    (f"Tomorrow is still {t_sk.split('.')[0]}, so you gain nothing by resting. " if same
                     else f"Tomorrow opens up to {t_sk.split('.')[0]}, so you've only moved today. ")
                    + f"Two days out you're {lost:.0f}% less fit than if you'd done it."))
    return out


def _assume_green(s):
    from ..analysis.recovery import Signal
    return Signal(s.name, GREEN, s.today, s.baseline, "assumed normal")


SUBTYPE = {"MountainBikeRide": "mountain bike", "EMountainBikeRide": "mountain bike", "mountain_biking": "mountain bike",
           "Ride": "road ride", "road_biking": "road ride", "GravelRide": "gravel ride", "gravel_cycling": "gravel ride",
           "VirtualRide": "trainer ride", "Run": "run", "TrailRun": "trail run", "trail_running": "trail run",
           "BackcountrySki": "ski tour", "backcountry_skiing": "ski tour", "NordicSki": "nordic ski",
           "Hike": "hike", "hiking": "hike", "Walk": "walk", "walking": "walk",
           "AlpineSki": "resort skiing", "resort_skiing_snowboarding_ws": "resort skiing",
           "resort_skiing": "resort skiing", "Snowboard": "resort skiing"}


def usual_kind(p: Profile, sport: str) -> str | None:
    """What this athlete actually does in this sport lately: MTB vs road, trail vs road run."""
    cutoff = (date.fromisoformat(p.as_of) - timedelta(days=60)).isoformat()
    kinds = [SUBTYPE.get(a.raw_type or "") for a in p.activities if a.sport == sport and a.date >= cutoff]
    kinds = [k for k in kinds if k]
    return max(set(kinds), key=kinds.count) if kinds else None


def example_ride(p: Profile, sport: str, verdict: str, mins: int, ceiling: int | None,
                 kind: str | None = None) -> str:
    """Point at a real ride from your history that fits today, and say how today differs."""
    if verdict in (REST, LIGHT) or ceiling is None:
        return ""
    kind = kind or usual_kind(p, sport)
    cutoff = (date.fromisoformat(p.as_of) - timedelta(days=180)).isoformat()
    pool = [a for a in p.activities if a.sport == sport and a.date >= cutoff and a.date < p.as_of
            and a.moving_s and 0.7 * mins <= a.moving_s / 60 <= 1.3 * mins
            and (kind is None or SUBTYPE.get(a.raw_type or "") == kind)]
    if not pool:
        return ""
    names = [a.name for a in pool if a.name]
    usual = max(set(names), key=names.count) if names else None
    group = [a for a in pool if a.name == usual] if usual and names.count(usual) >= 2 else pool
    low_ = getattr(p, "_today_low", None) or (ceiling - 15)
    mid = (low_ + ceiling) / 2
    pick = (max(group, key=lambda a: a.elev_m or 0) if verdict == HARD
            else min(group, key=lambda a: abs((a.avg_hr or 0) - mid)))
    h, m = divmod(round(pick.moving_s / 60), 60)
    dur = f"{h}h {m:02d}m" if h else f"{m} min"
    climb = f", {round(pick.elev_m * 3.281 / 50) * 50:,} ft of climbing" if pick.elev_m else ""
    when = date.fromisoformat(pick.date).strftime("%b %-d")
    label = pick.name or f"{kind or LABEL[sport].lower()} on {when}"
    lead = f"Your usual {label}" if usual and pick.name == usual and names.count(usual) >= 2 else f"Something like your {label} ({when})"
    s = f"{lead} works: {dur}{climb}."
    if pick.avg_hr and verdict in (EASY, STEADY):
        diff = round(pick.avg_hr - ceiling)
        low_ = getattr(p, "_today_low", None)
        if low_ and pick.avg_hr < low_ - 3:
            s += (f" You averaged {pick.avg_hr:.0f} that day, under today's range: push the climbs a little "
                  f"more to keep it above {low_}.")
            diff = None
        if diff is None:
            pass
        elif diff > 2:
            s += (f" You averaged {pick.avg_hr:.0f} that day. Today the same route sits {diff}+ beats lower, "
                  f"so climb slower than you want to.")
        else:
            s += f" You averaged {pick.avg_hr:.0f} that day, right where today should be."
    if verdict in (EASY, STEADY):
        s += " Bring a friend: if you can't hold a conversation on the climbs, you're going too hard."
    return s


NOUN = {"mountain bike": "mountain bike ride", "road ride": "road ride", "gravel ride": "gravel ride",
        "trainer ride": "trainer ride", "trail run": "trail run", "run": "run", "ski tour": "ski tour",
        "nordic ski": "nordic ski", "hike": "hike", "walk": "walk", "resort skiing": "day of resort skiing"}


def what_how(rx: Rx, kind: str | None, mins: int, low, ceiling, steps) -> tuple[str, list[str]]:
    """What to do (one line), and how to do it (a few plain instructions)."""
    noun = NOUN.get(kind or "", None) or {BIKE: "ride", RUN: "run", SKI: "ski tour", WALK: "walk"}.get(rx.sport, "session")
    v = rx.verdict
    if v == REST:
        what = "Day off."
        how = [f"If you want to move, a slow walk of {mins} minutes at most. Heart rate under {ceiling}."]
    elif v == LIGHT:
        what = f"No workout. A relaxed walk, {mins} minutes at most."
        how = [f"Heart rate under {ceiling}. A stroll, not a power walk.",
               "No riding, no running, no speed-walking to rack up steps."]
    elif v == EASY:
        what = f"An easy {noun}, {mins} minutes at {round((low + ceiling) / 2)} ({low}–{ceiling})."
        how = [f"Hold {round((low + ceiling) / 2)}, never over {ceiling}. Easy enough to take a phone call the whole way without the other person hearing you breathe.",
               f"On the climbs, shift down, spin or walk the steep pitches to stay in range."]
    elif v == STEADY:
        what = f"A steady {noun}, up to {mins} minutes at {round((low + ceiling) / 2)} ({low}–{ceiling})."
        how = [f"Hold {round((low + ceiling) / 2)}, never over {ceiling}. You could take a phone call, "
               f"but you'd keep it short.",
               f"No chasing, no attacking climbs, no intervals."]
    else:
        what = f"A hard {noun}: 4 × 8 minutes at {low}–{ceiling - 3}, {mins} minutes total."
        how = list(steps) + [f"Never over {ceiling}, even on the last one."]
    if rx.sport in (BIKE, SKI) and v in (EASY, STEADY):
        how.append(f"A short spike on a steep pitch is fine. If you're over {ceiling} for a full minute, ease off.")
    if rx.example:
        how.insert(1, rx.example)
    return what, how


def _mix(p: Profile, day: str, days: int) -> dict[str, float]:
    cut = (date.fromisoformat(day) - timedelta(days=days)).isoformat()
    out: dict[str, float] = {}
    for a in p.activities:
        if cut <= a.date < day:
            out[a.sport] = out.get(a.sport, 0.0) + (a.moving_s or 0) / 3600
    return out


def easy_band(z) -> tuple[int, int]:
    """Easy day: the top 10 beats of your Easy zone. That sits well under your aerobic threshold
    (the top of Steady), which is what easy means physiologically: below LT1, all conversation."""
    ceiling = z.top("Z1 Recovery")
    return ceiling - 10, ceiling


def _band(verdict: str, p: Profile) -> tuple[int, int] | None:
    z = p.zones
    if not z:
        return None
    if verdict == EASY:
        return easy_band(z)
    if verdict == STEADY:
        return z.bottom("Z2 Aerobic"), z.top("Z2 Aerobic")
    return None


def kind_stats(p: Profile, day: str, days: int = 150) -> dict[str, dict]:
    """What each kind of session actually does to YOUR heart: median average HR, typical
    length, how often. A fit athlete's hike may sit at 110; that's not aerobic work for them."""
    cut = (date.fromisoformat(day) - timedelta(days=days)).isoformat()
    by: dict[str, list] = {}
    for a in p.activities:
        k = SUBTYPE.get(a.raw_type or "")
        if not k or not (cut <= a.date < day) or not a.avg_hr or (a.moving_s or 0) < 20 * 60:
            continue
        by.setdefault(k, []).append(a)
    out = {}
    for k, acts in by.items():
        hrs = sorted(a.avg_hr for a in acts)
        mins = sorted(a.moving_s / 60 for a in acts)
        out[k] = {"n": len(acts), "hr": hrs[len(hrs) // 2], "hr_hi": hrs[int(len(hrs) * 0.8)],
                  "mins": mins[len(mins) // 2], "sport": acts[0].sport}
    return out


SPORT_OF = {"mountain bike": BIKE, "road ride": BIKE, "gravel ride": BIKE, "trainer ride": BIKE,
            "run": RUN, "trail run": RUN, "ski tour": SKI, "nordic ski": SKI, "hike": WALK, "walk": WALK,
            "resort skiing": SKI}
CONTROLLED = {"road ride", "gravel ride", "trainer ride", "run", "hike"}   # easy to hold a steady HR


def choose_activity(verdict: str, p: Profile, day: str, planned: dict | None = None,
                    avoid: str | None = None) -> tuple[str, str | None, str, int | None]:
    """Which activity, and why that one. Returns (sport, kind, reason, max minutes or None).

    First rule: it has to actually put YOUR heart in today's range. That's judged from your own
    sessions of that kind (median average HR over the last 5 months), not a generic rule.
    Then: balance (do less of what's dominated the last two weeks) and control (terrain that
    lets you hold an easy HR; mountain bike climbs force spikes)."""
    if verdict == REST:
        return WALK, "walk", "Rest is the session today. Walking keeps you loose without adding anything to recover from.", None
    if verdict == LIGHT:
        return WALK, "walk", ("A walk moves blood through tired legs and speeds recovery without adding load. "
                              "Anything with a climb or a pace would."), None

    mix14 = _mix(p, day, 14)
    for k_, h_ in (planned or {}).items():          # days already planned count toward the mix
        mix14[k_] = mix14.get(k_, 0.0) + h_
    tot = sum(v for k, v in mix14.items() if k != WALK) or 0.0
    stats = kind_stats(p, day)
    skied14 = mix14.get(SKI, 0.0) > 0
    band = _band(verdict, p)

    if verdict == HARD or not band or not stats:
        pool = {k: v for k, v in stats.items() if SPORT_OF.get(k) != WALK} or stats
        main = max(pool, key=lambda k: pool[k]["n"]) if pool else None
        return (SPORT_OF.get(main, BIKE), main,
                "Do the hard efforts on a steady climb or a flat road where you control the effort. "
                "Technical trail breaks intervals into spikes and rests.", None)

    lo, hi = band
    best, best_score, notes = None, -99.0, {}
    for k, st in stats.items():
        if k == "walk" or st["n"] < 3:
            continue
        if SPORT_OF.get(k) == SKI and not skied14:
            continue
        hr = st["hr"]
        fits = lo - 3 <= hr <= hi + 2
        reach = st["hr_hi"] >= lo           # your harder sessions of this kind do reach it
        share = mix14.get(SPORT_OF.get(k, BIKE), 0.0) / tot if tot else 0.0
        score = ((4 if fits else 1.5 if reach else -10) + 2 * (1 - share) + (1 if k in CONTROLLED and verdict == EASY else 0)
                 - (1.5 if k == avoid else 0))
        notes[k] = (fits, reach, hr, st, share)
        if score > best_score:
            best, best_score = k, score
    if best is None:
        return BIKE, None, "Your main sport, kept easy.", None

    fits, reach, hr, st, share = notes[best]
    sport = SPORT_OF.get(best, BIKE)
    noun = NOUN.get(best, best)
    parts = []
    if fits:
        parts.append(f"Your {noun}s average {hr:.0f}, " + (f"right in today's {lo}–{hi}." if lo <= hr <= hi
                     else f"right around today's {lo}–{hi}."))
    else:
        parts.append(f"Your {noun}s usually average {hr:.0f}, under today's {lo}–{hi}. Pick a route with steady "
                     f"climbing so you spend the time in range; your harder ones get there.")
    dominant = max(mix14, key=mix14.get) if mix14 else None
    if dominant and SPORT_OF.get(best) != dominant and tot and mix14.get(dominant, 0) / tot >= 0.6:
        dom = {BIKE: "the bike", RUN: "running", SKI: "skis", WALK: "your feet"}.get(dominant, dominant)
        parts.append(f"And {mix14[dominant] / tot * 100:.0f}% of your last two weeks was on {dom}: "
                     + ("a run works different muscles and loads your bones, which cycling doesn't."
                        if sport == RUN else "this gives your legs a different job."))
    if best == "mountain bike" and verdict == EASY:
        parts.append("Pick your flattest loop: steep climbs make an easy day impossible.")
    elif best in ("road ride", "gravel ride", "trainer ride") and verdict == EASY:
        parts.append("Smooth terrain lets you hold an easy heart rate; trail climbs force it up.")
    cap = None
    if sport == RUN:
        cut30 = (date.fromisoformat(day) - timedelta(days=30)).isoformat()
        if sum(1 for a in p.activities if a.sport == RUN and cut30 <= a.date < day) < 3:
            cap = 40
            parts.append("Keep it to 40 minutes: your legs aren't used to much running right now.")
    return sport, best, " ".join(parts), cap


SNOW = {"ski tour", "nordic ski", "resort skiing"}
BIKES = {"mountain bike", "road ride", "gravel ride", "trainer ride"}


def alternatives(p: Profile, rx: Rx, chosen: str | None) -> list[tuple[str, str, str, str]]:
    """Other ways to do today's session, from the things you actually do, each judged by what it
    does to YOUR heart (your own sessions of that kind over the last year) and by the season.
    Returns (title, note, tone, short name), best first. Same heart rate whatever you pick."""
    if rx.verdict in (REST,) or not p.zones or rx.hr_ceiling is None:
        return []
    lo = rx.hr_low if rx.hr_low is not None else rx.hr_ceiling - 15
    hi = rx.hr_ceiling
    aim = round((lo + hi) / 2)
    month = date.fromisoformat(rx.date).month
    winter, deep = month in (11, 12, 1, 2, 3, 4), month in (12, 1, 2, 3)
    stats = kind_stats(p, rx.date, 400)
    runs30 = sum(1 for a in p.activities if a.sport == RUN and
                 (date.fromisoformat(rx.date) - timedelta(days=30)).isoformat() <= a.date < rx.date)
    out = []
    for k, st in stats.items():
        if k == chosen or k == "walk" or st["n"] < 3:
            continue
        if k in SNOW and not winter:
            continue
        if rx.verdict == HARD and k in ("hike", "resort skiing", "nordic ski"):
            continue
        if rx.verdict == STEADY and k == "resort skiing":
            continue
        noun = NOUN.get(k, k)
        hr, hr_hi = st["hr"], st["hr_hi"]
        if rx.verdict == HARD:
            mins = rx.max_minutes or 75
            title = f"{noun[:1].upper() + noun[1:]}: 4 × 8 minutes at {lo}–{hi - 3}, {mins} minutes total"
        elif k == "resort skiing":
            title = "A few easy hours of resort laps"
        else:
            base = rx.max_minutes or 60
            mins = base
            if k in BIKES and rx.verdict == EASY:
                mins = max(base, 60)                    # low impact: you can ride longer than you run
            if k in ("run", "trail run") and runs30 < 3:
                mins = min(mins, 40)
            if k == "ski tour":
                mins = max(base, 75)
            title = f"{noun[:1].upper() + noun[1:]}, {mins} minutes at {aim} ({lo}–{hi})"
        # how it fits your heart
        if rx.verdict == HARD:
            note, tone = ({"run": "On a flat road, a track or a treadmill, where nothing interrupts the efforts.",
                           "trail run": "On a steady uphill trail, walking back down between efforts.",
                           "ski tour": "On the skin track, up a steady slope: one effort per climb."}
                          .get(k, "On a steady climb or a flat road where you control the effort."), "good")
        elif k == "resort skiing":
            note, tone = (f"Your ski days average {hr:.0f}: the lifts give you rest between runs, "
                          f"so it counts as an easy day as long as you cruise."), "good"
        elif lo - 3 <= hr <= hi + 3:
            note, tone = f"Your {noun}s average {hr:.0f}, right in range.", "good"
        elif hr > hi + 3:
            note, tone = (f"Your {noun}s average {hr:.0f}, over today's {hi}. Pick a flat route and keep "
                          f"it gentle, or it won't stay {'easy' if rx.verdict in (EASY, LIGHT) else 'in range'}."), "ok"
        elif hr_hi >= lo:
            note, tone = f"Your {noun}s usually average {hr:.0f}. Push the climbs a little to stay above {lo}.", "ok"
        else:
            if rx.verdict in (STEADY, HARD):
                continue                                 # can't reach the range: not an option
            note, tone = f"Your {noun}s average {hr:.0f}, under today's range: fine for an easy day.", "ok"
        if k == "mountain bike" and rx.verdict in (EASY, LIGHT):
            note += " Climbs spike it: spin, or walk the steep pitches."
        if k == "mountain bike" and deep:
            note += " Only if the trails are clear of snow."
        if k in ("road ride", "gravel ride") and deep:
            note += " If the roads are wet or icy, do it on the trainer."
        if k in ("run", "trail run") and deep:
            note += " If the trails are snowed in, a treadmill gives you the same session."
        if k in ("run", "trail run") and runs30 < 3:
            note += " Your legs aren't used to running right now: keep it short."
        short = {"resort skiing": "a day of resort skiing"}.get(k, ("an " if noun[0] in "aeiou" else "a ") + noun)
        out.append((title, note, tone, short))
    out.sort(key=lambda o: 0 if o[2] == "good" else 1)
    return out[:4]


def _hm_short(h: float) -> str:
    hh, mm = int(h), int(round((h - int(h)) * 60))
    return f"{hh}h {mm:02d}m" if hh else f"{mm} minutes"


def _hm_words(h: float) -> str:
    hh, mm = int(h), round((h - int(h)) * 60)
    return f"{hh}h {mm:02d}m" if hh else f"{mm} minutes"


def why_story(verdict: str, p: Profile, day: str, sb, reading) -> tuple[str, str]:
    """The Why as a coach says it: what you need, which signals point there, and what they mean
    together. One reading of all of it, not a list of numbers (the numbers sit under More)."""
    need = {REST: "You need a day off.", LIGHT: "Move today, but don't train.",
            EASY: "Train today, but take it easy.", STEADY: "Train today: steady, not hard.",
            HARD: "You're ready to go hard."}[verdict]
    w = sorted((x for x in p.wellness if x.date <= day), key=lambda x: x.date)

    def avg(vals):
        vals = [v for v in vals if v is not None]
        return sum(vals) / len(vals) if vals else None

    hrv7, hrv60 = avg(x.hrv for x in w[-7:]), avg(x.hrv for x in w[-67:-7])
    rhr7, rhr60 = avg(x.rhr for x in w[-7:]), avg(x.rhr for x in w[-67:-7])
    hrv_dir = None if not (hrv7 and hrv60) else ("down" if hrv7 < hrv60 * 0.95 else "up" if hrv7 > hrv60 * 1.05 else "flat")
    rhr_dir = None if not (rhr7 and rhr60) else ("up" if rhr7 >= rhr60 + 1.5 else "down" if rhr7 <= rhr60 - 1.5 else "flat")
    debt = sb.balance if sb else 0.0
    rising = bool(sb and len(sb.nights) >= 4 and sb.nights[-1].debt > sb.nights[-4].debt + 0.25)
    y = p.yesterday
    load_up = bool(y and y.ctl > 5 and y.atl / y.ctl > 1.15)

    bad, good = [], []
    if debt >= 1.0:
        bad.append(f"sleep debt is stacking up ({_hm_words(debt)} owed)" if rising
                   else f"you're carrying {_hm_words(debt)} of sleep debt")
    if hrv_dir == "down":
        bad.append(f"HRV is trending down ({hrv7:.0f} this week against your usual {hrv60:.0f})")
    if rhr_dir == "up":
        bad.append(f"resting HR is creeping up ({rhr7:.0f} against your usual {rhr60:.0f})")
    if load_up:
        bad.append(f"your last week is {(y.atl / y.ctl - 1) * 100:.0f}% above your normal training")
    if hrv_dir == "flat" and rhr_dir == "flat":
        good.append("HRV and resting HR are both holding")
    else:
        if hrv_dir in ("up", "flat"):
            good.append("HRV is holding" if hrv_dir == "flat" else "HRV is trending up")
        if rhr_dir in ("flat", "down"):
            good.append("resting HR is holding" if rhr_dir == "flat" else "resting HR is coming down")

    def join(xs):
        return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]

    parts = []
    if bad:
        parts.append(join(bad)[0].upper() + join(bad)[1:] + ".")
    if hrv_dir == "down" and (rhr_dir == "up" or debt >= 2.0):
        parts.append("Together that means you're fatigued and not fully recovered. Easy work keeps the "
                     "fitness; anything harder digs the hole deeper.")
    elif hrv_dir == "down":
        parts.append("Resting HR is holding, so it's early. Back off now and it turns around in a few days, "
                     "not a few weeks.")
    elif bad:
        tail = {HARD: "Go hard, then make tonight's sleep count.",
                STEADY: "Steady work is fine; intensity is what would tip it over."}.get(verdict, "Today is about not tipping it over.")
        parts.append(f"But {join(good)}, so your body is coping. {tail}" if good
                     else "That's fatigue building. Today is about letting it clear.")
    elif good:
        parts.append(join(good)[0].upper() + join(good)[1:] + ": you're absorbing what you're doing.")
    return need, " ".join(parts)


def why_lead(verdict: str, p: Profile, day: str, reading) -> tuple[str, str, str]:
    """The headline of the Why: whatever actually drove today's call."""
    y = p.yesterday
    if y and y.ctl > 5 and verdict in (REST, LIGHT, EASY) and getattr(reading, "tone", "") == "good":
        ratio = y.atl / y.ctl
        if ratio > 1.15:
            return ("Too much, too fast", "bad" if ratio > 1.5 else "ok",
                    f"Your last 7 days are {(ratio - 1) * 100:.0f}% above your 6-week normal. Recovery looks fine "
                    f"this morning, but a spike that size shows up as fatigue a day or two later.")
        big = p.daily_load.get((date.fromisoformat(day) - timedelta(days=1)).isoformat(), 0.0)
        return ("Absorbing yesterday", "ok",
                f"You recovered well overnight, but yesterday was {big / max(y.ctl, 1):.1f}× a normal day. "
                f"Easy today is how it turns into fitness instead of fatigue.")
    return (getattr(reading, "title", ""), getattr(reading, "tone", "none"), getattr(reading, "says", ""))


def diagnosis(verdict: str, p: Profile, day: str, reading, sb) -> str:
    """The prescription's first line: what you need today, and the specific reason."""
    d0 = date.fromisoformat(day)
    y = (d0 - timedelta(days=1)).isoformat()
    avg = sum(p.daily_load.get((d0 - timedelta(days=n)).isoformat(), 0.0) for n in range(1, 29)) / 28
    ld = p.daily_load.get(y, 0.0)
    causes = []
    if avg and ld > 1.5 * avg:
        acts = sorted([a for a in p.activities if a.date == y], key=lambda a: p.session_load.get(a.id, 0), reverse=True)
        if acts:
            a = acts[0]
            h, m = divmod(round(a.moving_s / 60), 60)
            dur = f"{h}h {m:02d}m" if h else f"{m} minutes"
            label = (a.name or a.sport).strip()
            causes.append(f"yesterday's {label} ({dur}) was {ld / avg:.1f}× a normal day")
        else:
            causes.append(f"yesterday was {ld / avg:.1f}× a normal day")
    last = sb.nights[-1] if sb and sb.nights else None
    if last and last.slept is not None and last.slept < last.need - 1.0:
        h, m = int(last.slept), round((last.slept - int(last.slept)) * 60)
        sc = f", score {last.score:.0f}" if last.score else ""
        causes.append(f"you slept {h}h {m:02d}m")
    elif last and last.score is not None and last.score < 60:
        causes.append(f"last night's sleep scored {last.score:.0f}")
    if sb and sb.state in ("owed", "debt", "deep_debt") and not any(c.startswith(("you slept", "last night")) for c in causes):
        causes.append(f"you're {_hm_short(sb.balance)} behind on sleep")
    key = getattr(reading, "key", "")
    if key in ("trending_low", "not_absorbing"):
        causes.append("your HRV has been below your normal range all week")
    elif key in ("illness",):
        causes.append("resting HR is up and HRV is down, which can mean you're fighting something")
    elif key == "rhr_up":
        causes.append("your resting heart rate is up")
    elif key == "saturation":
        causes.append("you're carrying heavy fatigue even though HRV looks high")
    elif key == "one_off_dip":
        causes.append("HRV dropped last night with no training reason")

    need = {REST: "Day off today", LIGHT: "Light day today: a walk, nothing more", EASY: "Easy day today",
            STEADY: "Steady day today: aerobic time, no intervals", HARD: "Hard day today"}[verdict]
    if verdict == HARD:
        return "You're ready for a hard day: you're recovered and nothing hard in the last two days."
    if not causes:
        if verdict == STEADY:
            return "You need a steady day today. You're recovered, but you've worked hard recently: build aerobic time, not intensity."
        return f"{need}."
    causes = causes[:3]
    because = causes[0] if len(causes) == 1 else ", ".join(causes[:-1]) + " and " + causes[-1]
    return f"{need}. {because[:1].upper()}{because[1:]}."


def minimum_dose(p: Profile, rx: "Rx", kind: str | None = None) -> str:
    """The least you can do TODAY and keep the fitness you have.

    Evidence: fitness holds with volume cut by up to two thirds as long as frequency and
    intensity are kept (Hickson 1981, 1985; Spiering 2021). So the floor is a shorter version
    of today's session, never a different one: on a hard day keep the hard minutes and drop the
    filler; on an easy day 30 minutes in range keeps the habit and the aerobic signal."""
    if rx.verdict == REST:
        return "Nothing. A day off costs you no fitness; resting is how you keep it today."
    if rx.verdict == LIGHT:
        return "Nothing, or a 20-minute walk. One easy day costs you no fitness."
    if not p.zones:
        return ""
    z = p.zones
    noun = NOUN.get(kind or "", None) or {BIKE: "ride", RUN: "run", SKI: "ski tour", WALK: "walk"}.get(rx.sport, "session")
    if rx.verdict == HARD:
        lo, hi = z.bottom("Z4 Threshold"), z.top("Z4 Threshold")
        return (f"40 minutes: 10 easy, 2 × 10 minutes at {lo}–{hi} with 4 easy between, 6 easy to finish. "
                f"Keep the hard part, cut the rest. That holds your fitness.")
    floor = 30 if (rx.max_minutes or 0) >= 60 else 20
    if not rx.max_minutes or rx.max_minutes <= floor or not rx.hr_low:
        return ""
    return (f"A {floor}-minute {noun} at {rx.hr_low}–{rx.hr_ceiling}. That holds your fitness. "
            f"The full {rx.max_minutes} builds it.")
