"""The rolling plan: today's prescription, then the days after it, only as far as we can honestly see.

Every morning it's rebuilt from scratch: today's real Rx (from last night's sleep and HRV),
then each following day decided the same way the daily Rx is, assuming you sleep normally
and do what's planned. If you don't, tomorrow's rebuild sees it and the plan changes.

How far it looks depends on how settled you are: recovered and rested, about a week;
carrying fatigue or sleep debt, only a few days, because the next nights decide the rest.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from ..analysis import load as la
from ..analysis.recovery import Recovery
from ..profile import Profile
from ..sports import WALK
from . import engine as eng
from .engine import EASY, HARD, LIGHT, REST, STEADY, Rx

KIND = {REST: "Day off", LIGHT: "Walk", EASY: "Easy", STEADY: "Steady", HARD: "Hard"}


@dataclass
class PlanDay:
    date: str
    verdict: str
    title: str          # "Easy run, 40 min"
    detail: str         # the full session
    confidence: str     # Today | Likely | Probable | Sketch
    reason: str = ""
    load: float = 0.0
    was: str = ""       # the verdict yesterday's plan had for this day, if it changed
    was_title: str = ""
    options: str = ""   # other ways to do it
    phase: str = ""     # the goal programme's phase that week
    because: str = ""


@dataclass
class Plan:
    days: list[PlanDay] = field(default_factory=list)
    horizon: int = 0
    note: str = ""


def _rest_days(p: Profile, day: str) -> set[int]:
    """Weekdays you almost never train (from the last 12 weeks), so the plan doesn't fight your life."""
    d0 = date.fromisoformat(day)
    trained = {a.date for a in p.activities if a.sport != WALK}
    out = set()
    for wd in range(7):
        days = [d0 - timedelta(days=n) for n in range(1, 85) if (d0 - timedelta(days=n)).weekday() == wd]
        if days and sum(x.isoformat() in trained for x in days) / len(days) < 0.1:
            out.add(wd)
    return out


def _horizon(rx: Rx) -> tuple[int, str]:
    tone = getattr(rx.reading, "tone", "none") if rx.reading else "none"
    state = rx.sleep.state if rx.sleep else "unknown"
    if tone == "bad" or state == "deep_debt" or rx.verdict in (REST, LIGHT):
        return 4, "You're carrying fatigue, so the next few nights decide what comes after."
    if tone == "ok" or state in ("owed", "debt", "slightly_owed"):
        return 5, "You're not fully recovered, so the next nights decide what comes after."
    return 8, "You're recovered, so we can see about a week ahead."


RUNS = {"run", "trail run"}


def _runs30(p: Profile, d: str) -> int:
    cut = (date.fromisoformat(d) - timedelta(days=30)).isoformat()
    return sum(1 for a in p.activities if a.sport == eng.RUN and cut <= a.date < d)


def _pick(p: Profile, v: str, d: str, planned: dict, avoid: str | None,
          ban: set | None = None, prefer: str | None = None) -> tuple[str, str | None, str]:
    try:
        sport, kind, _, _ = eng.choose_activity(v, p, d, planned, avoid, ban, prefer)
    except Exception:
        return eng.BIKE, None, "session"
    noun = eng.NOUN.get(kind or "", None) or {eng.BIKE: "ride", eng.RUN: "run", eng.SKI: "ski tour", WALK: "walk"}.get(sport, "session")
    return sport, kind, noun


def _title(p: Profile, v: str, d: str, rx: Rx | None = None, n: str = "session") -> str:
    if rx is not None:
        return rx.what.rstrip(".") if rx.what else eng.HEADLINE[v]
    z = p.zones
    if v == REST:
        return "Day off"
    if v == LIGHT:
        return "A walk, 30 to 45 minutes"
    if v == HARD:
        return f"Hard {n}: 4 × 8 minutes at {z.bottom('Z4 Threshold')}–{z.top('Z4 Threshold')}"
    if v == STEADY:
        lo, hi = z.bottom("Z2 Aerobic"), z.top("Z2 Aerobic")
        return f"Steady {n}, up to 90 minutes at {round((lo + hi) / 2)} ({lo}–{hi})"
    lo, hi = eng.easy_band(z)
    return f"Easy {n}, 60 minutes at {round((lo + hi) / 2)} ({lo}–{hi})"


def build(p: Profile, rx: Rx) -> Plan:
    if not p.ready or not p.yesterday or not p.zones:
        return Plan(note="The plan starts once there's a few weeks of training, sleep and HRV.")
    n, why = _horizon(rx)
    rest_wd = _rest_days(p, rx.date)
    good = Recovery(rx.date, *[eng._assume_green(s) for s in rx.recovery.signals])

    days = [PlanDay(rx.date, rx.verdict, _title(p, rx.verdict, rx.date, rx),
                    " ".join(rx.how) if rx.how else rx.do, "Today", rx.diagnosis)]
    today_load = eng._rx_load(p, rx) if rx.verdict != REST else 0.0
    days[0].load = today_load
    state = la.step(p.yesterday, today_load, rx.date)
    loads = [p.yesterday.load, today_load]
    hard_days = [rx.date] if rx.verdict == HARD else []
    planned = {rx.sport: (rx.max_minutes or 60) / 60} if rx.verdict not in (REST, LIGHT) else {}
    last_kind = rx.sport if rx.sport == eng.RUN else None
    # Running is the one sport that punishes a jump in volume (bones and tendons adapt slower than
    # heart and lungs). If you haven't been running, at most one run every other day, short and easy.
    new_runner = _runs30(p, rx.date) < 6
    last_run = rx.date if rx.sport == eng.RUN and rx.verdict not in (REST, LIGHT) else None
    d0 = date.fromisoformat(rx.date)
    from .. import goals as gl
    prog = getattr(p, "program", None)
    days[0].phase = (gl.daily(prog, d0) or {}).get("phase", "")
    for k in range(1, n):
        d = (d0 + timedelta(days=k)).isoformat()
        dd = d0 + timedelta(days=k)
        v, _ = eng.decide(eng.Inputs(good, state, loads[-2:]))
        reason = ""
        gday = gl.daily(prog, dd)
        recent_hard = [h for h in hard_days if (date.fromisoformat(d) - date.fromisoformat(h)).days < 7]
        if v == HARD and len(recent_hard) >= 2:
            v, reason = STEADY, "Two hard days already this week."
        if gday and v == HARD:
            mon = (dd - timedelta(days=dd.weekday())).isoformat()
            if sum(1 for h in hard_days if h >= mon) >= gday["hard_days"]:
                v, reason = STEADY, f"{gday['phase']} week: {gday['hard_days']} hard day{'s' if gday['hard_days'] != 1 else ''}."
        if (d0 + timedelta(days=k)).weekday() in rest_wd and v != REST and not (gday and gday.get("long_minutes")):
            v, reason = REST, f"You almost never train on {(d0 + timedelta(days=k)).strftime('%A')}s."
        gsport = (gday or {}).get("sport") or None
        if gday and gday.get("long_minutes") and v in (HARD,):
            v = STEADY
        ban = set()
        if new_runner and (v in (STEADY, HARD) or (last_run and (date.fromisoformat(d) - date.fromisoformat(last_run)).days < 2)):
            ban = set(RUNS)
        if last_kind in RUNS:
            ban |= RUNS
        if gsport == eng.RUN and v == EASY and last_kind not in RUNS:
            ban -= RUNS                                  # the goal is a run: easy runs are the plan
        long_m = (gday or {}).get("long_minutes") if v in (EASY, STEADY) else None
        if long_m and gsport:
            ban -= RUNS if gsport == eng.RUN else set()
        sport, kind, noun = _pick(p, v, d, planned, last_kind, ban, gsport) if v not in (REST, LIGHT) else (WALK, "walk", "walk")
        if long_m and gsport and sport != gsport:
            sport, kind, noun = gsport, None, {eng.RUN: "run", eng.BIKE: "ride", eng.SKI: "ski tour"}.get(gsport, "session")
        if sport == eng.RUN:
            last_run = d
        detail, load = eng._session(p, v, d, noun)
        if v not in (REST, LIGHT):
            planned[sport] = planned.get(sport, 0.0) + {HARD: 1.25, STEADY: 1.5, EASY: 1.0}[v]
        last_kind = kind if kind else (sport if sport == eng.RUN else None)
        if v == HARD:
            hard_days.append(d)
        conf = "Likely" if k == 1 else "Probable"     # past the horizon, outlook() calls it a Sketch
        detail = detail[:1].upper() + detail[1:] + "."
        if long_m:
            lo_e, hi_e = eng.easy_band(p.zones)
            load = eng._est_load(p, long_m, (lo_e + hi_e) / 2)
            title = f"Long {noun}, {long_m} minutes easy at {round((lo_e + hi_e) / 2)} ({lo_e}–{hi_e})"
            detail = (f"The week's long {noun}: {long_m} minutes, easy the whole way, {lo_e}–{hi_e}. "
                      f"Fuel every 45 minutes. This is the session that builds you for the goal.")
            reason = reason or f"{gday['phase']} week: long {noun}."
        elif gday and gday.get("race_day"):
            title, detail = "Race day", "Race day. Easy warm-up, start slower than you want to, and enjoy it."
        else:
            title = _title(p, v, d, n=noun)
        if gday and gday["phase"] == "Taper" and not long_m:
            reason = reason or "Taper: same intensity, less volume."
        pd = PlanDay(d, v, title, detail, conf, reason, load)
        pd.phase = (gday or {}).get("phase", "")
        if v not in (REST, LIGHT):
            from types import SimpleNamespace
            if v == HARD:
                lo_, hi_ = p.zones.bottom("Z4 Threshold"), p.zones.top("Z4 Threshold") + 3
            elif v == STEADY:
                lo_, hi_ = p.zones.bottom("Z2 Aerobic"), p.zones.top("Z2 Aerobic")
            else:
                lo_, hi_ = eng.easy_band(p.zones)
            shim = SimpleNamespace(verdict=v, hr_low=lo_, hr_ceiling=hi_, date=d,
                                   max_minutes={HARD: 75, STEADY: 90, EASY: 60}[v])
            alts = [o for o in eng.alternatives(p, shim, kind) if o[2] != "bad"
                    and not (o[3].endswith("run") and ban & RUNS)]
            if alts:
                names = [o[3] for o in alts[:2]]
                pd.options = "Or " + " or ".join(names) + "."
        days.append(pd)
        state = la.step(state, load, d)
        loads.append(load)
    note = (f"Planned {n} days out. {why} It assumes you sleep normally and do what's planned; "
            f"every morning it's rebuilt from your real sleep, HRV and training.")
    return Plan(days, n, note)


WORD = {REST: "a day off", LIGHT: "a walk", EASY: "easy", STEADY: "steady", HARD: "hard"}


def causes(p: Profile, rx: Rx, prev: Plan) -> list[tuple[str, int]]:
    """What happened between yesterday's plan and this morning, each tagged by which way it pushes:
    +1 makes you readier than the plan assumed, -1 less ready. Biggest first."""
    out = []
    y = (date.fromisoformat(rx.date) - timedelta(days=1)).isoformat()
    planned = next((d for d in prev.days if d.date == y), None)
    actual = p.daily_load.get(y, 0.0)
    if planned is not None:
        if planned.verdict in (REST, LIGHT) and actual > max(30.0, planned.load * 1.5):
            out.append((f"you trained yesterday when the plan said {WORD[planned.verdict]}", -1))
        elif planned.load > 10:
            r = actual / planned.load
            if r >= 1.3:
                out.append((f"yesterday went harder than planned (about {r:.1f}× the plan)", -1))
            elif actual < 5:
                out.append(("you skipped yesterday's session", +1))
            elif r <= 0.6:
                out.append(("yesterday was lighter than planned", +1))
    last = rx.sleep.nights[-1] if rx.sleep and rx.sleep.nights else None
    if last and last.slept is not None:
        h, m = int(last.slept), round((last.slept - int(last.slept)) * 60)
        if last.slept < last.need - 1.0:
            out.append((f"you slept {h}h {m:02d}m, short of the {eng._hm_words(last.need)} you needed", -1))
        elif last.slept >= last.need - 0.25 and (last.score or 80) >= 80:
            out.append((f"you slept well ({h}h {m:02d}m)", +1))
    w = sorted((x for x in p.wellness if x.date <= rx.date), key=lambda x: x.date)
    if w and w[-1].date == rx.date and w[-1].rhr:
        base = [x.rhr for x in w[-61:-1] if x.rhr]
        if len(base) >= 14:
            u = sum(base) / len(base)
            if w[-1].rhr >= u + 4:
                out.append((f"resting HR was up at {w[-1].rhr:.0f} (you're usually {u:.0f})", -1))
    if w and w[-1].date == rx.date and w[-1].hrv:
        base = [x.hrv for x in w[-61:-1] if x.hrv]
        if len(base) >= 14:
            u = sum(base) / len(base)
            if w[-1].hrv < u * 0.88:
                out.append((f"HRV dropped to {w[-1].hrv:.0f} (you're usually {u:.0f})", -1))
            elif w[-1].hrv > u * 1.1:
                out.append((f"HRV came in high at {w[-1].hrv:.0f} (you're usually {u:.0f})", +1))
    return out


def _sentence(xs: list[str]) -> str:
    s = xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]
    return s[:1].upper() + s[1:] + "."


def explain(direction: int, why: list[tuple[str, int]], rx: Rx | None = None) -> str:
    """Why a day moved up (+1) or down (-1): only the causes that push that way."""
    match = [t for t, sgn in why if sgn == direction]
    if match:
        return _sentence(match)
    if rx is not None and direction < 0 and rx.why_story:
        return rx.why_story.split(". ")[0].rstrip(".") + "."
    return ("Your numbers this morning came in better than the plan assumed." if direction > 0
            else "Your numbers this morning came in worse than the plan assumed.")


def adjust(p: Profile, rx: Rx, plan: Plan, prev: Plan) -> list[PlanDay]:
    """Mark every day whose plan changed since yesterday, and say why."""
    from .engine import ORDER
    was = {d.date: d.verdict for d in prev.days if not d.reason.startswith("You almost never")}
    was_t = {d.date: d.title for d in prev.days}
    why = causes(p, rx, prev)
    changed, first = [], True
    for d in plan.days:
        if d.date in was and was[d.date] != d.verdict:
            d.was = was[d.date]
            d.was_title = was_t.get(d.date, "")
            up = 1 if ORDER.index(d.verdict) > ORDER.index(d.was) else -1
            d.because = explain(up, why, rx) if first else "Knock-on from the change above."
            first = False
            changed.append(d)
    return changed


def ics(plan: Plan, url: str = "https://www.ceiling.app/calendar") -> str:
    """An iCalendar feed of the plan: all-day events, rebuilt every time a calendar app fetches it."""
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")

    from datetime import datetime, timezone
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Ceiling//Plan//EN", "CALSCALE:GREGORIAN",
           "METHOD:PUBLISH", "X-WR-CALNAME:Ceiling", "X-WR-CALDESC:Your training plan. Updates every morning.",
           "REFRESH-INTERVAL;VALUE=DURATION:PT1H", "X-PUBLISHED-TTL:PT1H"]
    for d in plan.days:
        start = d.date.replace("-", "")
        end = (date.fromisoformat(d.date) + timedelta(days=1)).isoformat().replace("-", "")
        desc = d.detail + ("\n\n" + d.reason if d.reason else "") + \
            f"\n\n{d.confidence}. Ceiling rewrites the plan every morning from your sleep, HRV and training.\n{url}"
        out += ["BEGIN:VEVENT", f"UID:{d.date}@ceiling.app", f"DTSTAMP:{stamp}",
                f"DTSTART;VALUE=DATE:{start}", f"DTEND;VALUE=DATE:{end}",
                f"SUMMARY:{esc(d.title)}", f"DESCRIPTION:{esc(desc)}", "TRANSP:TRANSPARENT", "END:VEVENT"]
    out.append("END:VCALENDAR")
    lines = []
    for l in out:                      # fold at 75 octets
        b = l.encode()
        while len(b) > 75:
            cut = 75
            while (b[cut] & 0xC0) == 0x80:
                cut -= 1
            lines.append(b[:cut].decode())
            b = b" " + b[cut:]
        lines.append(b.decode())
    return "\r\n".join(lines) + "\r\n"


# ---------------------------------------------------------------------------------------------
# The whole plan: everything from today to the end of the programme. One confidence scale for every
# day: Today (set), Likely (tomorrow), Probable (the rest of the honest horizon, above), then Sketch:
# drawn from the programme's week and how you usually train. Every day after today depends on how you
# follow the plan and how you recover, so each one firms up as it comes closer.

LONG, RACE = "long", "race"
LABEL = {REST: "Rest", LIGHT: "Walk", EASY: "Easy", STEADY: "Steady", HARD: "Hard", LONG: "Long", RACE: "Race"}
WEIGHT = {HARD: 1.0, STEADY: 1.2, EASY: 0.8}
# A session shorter than this isn't worth the drive: a hard day needs warm-up, intervals and cool-down.
FLOOR = {EASY: 40, STEADY: 45, HARD: 60}
CAP = {EASY: 90, STEADY: 150, HARD: 90}
EXTRA = {HARD: 0.5, STEADY: 1.2, EASY: 0.8}   # how the minutes above the floors are shared: volume goes to aerobic days
CONF = ("Today", "Likely", "Probable", "Sketch")
NO_PLAN_DAYS = 42


def _weekday_share(p: Profile, day: str) -> list[float]:
    """How often you train on each weekday (Mon=0), over the last 12 weeks."""
    d0 = date.fromisoformat(day)
    trained = {a.date for a in p.activities if a.sport != WALK}
    out = []
    for wd in range(7):
        days = [d0 - timedelta(days=n) for n in range(1, 85) if (d0 - timedelta(days=n)).weekday() == wd]
        out.append(sum(x.isoformat() in trained for x in days) / len(days) if days else 0.0)
    return out


def _programs(p: Profile) -> list:
    progs = getattr(p, "programs", None)
    if progs:
        return list(progs)
    one = getattr(p, "program", None)
    return [one] if one else []


def _gday(p: Profile, day: date) -> dict:
    from .. import goals as gl
    progs = getattr(p, "programs", None)
    both = getattr(gl, "daily_all", None)
    try:
        if progs and both:
            return both(progs, day) or {}
        return gl.daily(getattr(p, "program", None), day) or {}
    except Exception:
        return {}


def _week_hours(g: dict) -> float | None:
    if g.get("hours") is not None:
        return float(g["hours"])
    w = g.get("week")
    return float(w.hours) if w is not None and getattr(w, "hours", None) is not None else None


def plan_end(p: Profile, start: date) -> date:
    """The last day of the active plan(s), or about six weeks out when there's no plan."""
    ends = []
    for pr in _programs(p):
        g = getattr(pr, "goal", None)
        if g is not None and getattr(g, "kind", "") == "event" and getattr(g, "target_date", None):
            ends.append(date.fromisoformat(g.target_date))
        elif getattr(pr, "weeks", None):
            ends.append(date.fromisoformat(pr.weeks[-1].start) + timedelta(days=6))
    end = max(ends) if ends else start + timedelta(days=NO_PLAN_DAYS - 1)
    return min(max(end, start), start + timedelta(days=365))


def _minutes_in(title: str) -> int | None:
    import re
    m = re.search(r"(?<![×x] )\b(\d+)\s*min", title or "")
    return int(m.group(1)) if m else None


def _firm(p: Profile, rx: Rx, plan: Plan) -> dict[str, dict]:
    out = {}
    for d in plan.days:
        kind = d.verdict
        if d.title.startswith("Race day"):
            kind = RACE
        elif d.title.startswith("Long "):
            kind = LONG
        mins = (rx.max_minutes if d.confidence == "Today" else None) or _minutes_in(d.title)
        if mins is None and kind in WEIGHT:
            mins = {HARD: 75, STEADY: 90, EASY: 60}[kind]
        if kind in (REST, RACE):
            mins = None
        out[d.date] = dict(date=d.date, kind=kind, label=LABEL[kind], minutes=mins, phase=d.phase,
                           firm=True, title=d.title, conf=d.confidence, verdict=d.verdict, detail=d.detail,
                           options=d.options, reason=d.reason, because=d.because, was=d.was, was_title=d.was_title)
    return out


def _aim(p: Profile, kind: str) -> str:
    """The heart-rate aim for a sketched session, the same bands the daily Rx uses."""
    z = getattr(p, "zones", None)
    if z is None or kind in (REST, RACE, LIGHT):
        return ""
    try:
        if kind == HARD:
            lo, hi = z.bottom("Z4 Threshold"), z.top("Z4 Threshold") + 3
            return f"Intervals at {lo}–{hi}, with a warm-up and cool-down"
        if kind == STEADY:
            return f"Steady at {z.bottom('Z2 Aerobic')}–{z.top('Z2 Aerobic')}"
        lo, hi = eng.easy_band(z)
        return f"Easy the whole way, {lo}–{hi}"
    except Exception:
        return ""


def _share_minutes(budget: float, kinds: list[str]) -> list[int]:
    """Split a week's minutes across sessions: every session gets its floor, the rest by weight, up to a cap."""
    m = [float(FLOOR[k]) for k in kinds]
    extra = budget - sum(m)
    live = [i for i in range(len(kinds))]
    while extra > 1 and live:
        w = sum(EXTRA[kinds[i]] for i in live)
        nxt = []
        spent = 0.0
        for i in live:
            add = min(extra * EXTRA[kinds[i]] / w, CAP[kinds[i]] - m[i])
            m[i] += add
            spent += add
            if m[i] < CAP[kinds[i]] - 0.5:
                nxt.append(i)
        extra -= spent
        if len(nxt) == len(live):
            break
        live = nxt
    return [int(max(FLOOR[k], round(x / 5) * 5)) for k, x in zip(kinds, m)]


def outlook(p: Profile, rx: Rx, start: date | None = None, end: date | None = None,
            plan: Plan | None = None) -> list[dict]:
    """Every day from start to end: the honest horizon as planned, then a sketch from the programme.

    A pencilled-in day comes from the programme's week (phase, hours, hard days, the long session
    on the long day) and your habits: rest only on weekdays you train less than 10% of the time,
    hard days on the days you usually train, never back to back. With no plan, your recent
    weekly hours as an easy and steady pattern."""
    start = start or date.fromisoformat(rx.date)
    end = end or plan_end(p, start)
    if plan is None:
        plan = build(p, rx)
    firm = _firm(p, rx, plan) if plan.days else {}
    share = _weekday_share(p, rx.date)
    rest_wd = {wd for wd in range(7) if share[wd] < 0.1}
    from .. import goals as gl
    base_h = max(3.0, gl._recent_hours(p, date.fromisoformat(rx.date)))
    has_plan = bool(_programs(p))
    today = date.fromisoformat(rx.date)
    done_today = sum((a.moving_s or 0) / 60 for a in p.activities
                     if a.date == rx.date and a.sport != WALK and (a.moving_s or 0) >= 10 * 60)

    out: list[dict] = []
    kinds: dict[date, str] = {}            # kept across weeks so Sunday's hard day is seen by Monday
    mins: dict[date, int | None] = {}
    mon = start - timedelta(days=start.weekday())
    while mon <= end:
        week = [mon + timedelta(days=i) for i in range(7)]
        gds = {d: _gday(p, d) for d in week}
        gw = next((g for g in gds.values() if g), {})
        phase = gw.get("phase", "")
        hours = _week_hours(gw) if gw else None
        hours = hours if hours is not None else (None if has_plan and gw else round(base_h, 1))
        hard_n = gw.get("hard_days", 0) if gw else 0
        wobj = gw.get("week") if gw else None
        long_w = getattr(wobj, "long_minutes", None) if wobj is not None else None
        sport_w = gw.get("sport") if gw else None
        wnoun = {eng.RUN: "run", eng.BIKE: "ride", eng.SKI: "ski tour"}.get(sport_w, "session")
        for d in week:
            f = firm.get(d.isoformat())
            if f:
                kinds[d], mins[d] = f["kind"], f["minutes"]
        open_ = [d for d in week if d >= start and d not in kinds]   # whole weeks, so the hours split evenly
        for d in open_:
            g = gds[d]
            if g.get("race_day"):
                kinds[d], mins[d] = RACE, None
            elif g.get("rest"):                          # a day the plan rules out
                kinds[d], mins[d] = REST, None
            elif g.get("long_minutes"):
                kinds[d], mins[d] = LONG, int(round(g["long_minutes"] / 5) * 5)
        for d in open_:
            if d not in kinds and d.weekday() in rest_wd:
                kinds[d], mins[d] = REST, None
        # the day before a race is easy and short
        for d in open_:
            if d not in kinds and kinds.get(d + timedelta(days=1)) == RACE:
                kinds[d], mins[d] = EASY, 30
        busy = (HARD, LONG, RACE)
        need = max(0, hard_n - sum(1 for d in week if kinds.get(d) == HARD))
        for d in sorted((d for d in open_ if d not in kinds), key=lambda d: (-share[d.weekday()], d)):
            if need <= 0:
                break
            near = [kinds.get(d + timedelta(days=k)) or (firm.get((d + timedelta(days=k)).isoformat()) or {}).get("kind")
                    for k in (-1, 1)]
            if any(n in busy for n in near):
                continue
            kinds[d] = HARD
            need -= 1
        quiet = phase in ("Taper", "Recover", "Race")
        steady_n = 0
        for d in week:
            if d not in open_ or d in kinds:
                continue
            prev = kinds.get(d - timedelta(days=1)) or (firm.get((d - timedelta(days=1)).isoformat()) or {}).get("kind")
            if prev in busy or prev == STEADY or quiet or steady_n >= 2:
                kinds[d] = EASY
            else:
                kinds[d] = STEADY
                steady_n += 1
        # share the week's hours across the sessions still to place
        if hours is not None:
            used = sum(v for d, v in mins.items() if v and d in week)
            past = {d.isoformat() for d in week if d < start}       # what you already did this week counts
            used += sum((a.moving_s or 0) / 60 for a in p.activities if a.date in past and a.sport != WALK)
            if today in week and done_today:                        # today counts as what you did, not the plan
                used += done_today - (mins.get(today) or 0)
            todo = [d for d in open_ if kinds[d] in WEIGHT and d not in mins]
            left = hours * 60 - used
            # too many sessions for the hours: drop the least useful (easy, then steady, on the days you
            # train least) to rest, rather than shrink everything into 20-minute sessions
            while todo and sum(FLOOR[kinds[d]] for d in todo) > max(0.0, left) * 1.1:
                cand = [d for d in todo if kinds[d] == EASY] or [d for d in todo if kinds[d] == STEADY]
                if not cand:
                    break
                d = min(cand, key=lambda d: (share[d.weekday()], -d.toordinal()))
                kinds[d], mins[d] = REST, None
                todo.remove(d)
            for d, m in zip(todo, _share_minutes(left, [kinds[d] for d in todo])):
                mins[d] = m
        else:
            for d in open_:
                if kinds[d] in WEIGHT and d not in mins:
                    mins[d] = {HARD: 60, STEADY: 75, EASY: 45}[kinds[d]]
        for d in week:
            if not (start <= d <= end):
                continue
            f = firm.get(d.isoformat())
            if f:
                row = dict(f)
                row["phase"] = row["phase"] or phase
            else:
                k = kinds[d]
                row = dict(date=d.isoformat(), kind=k, label=LABEL[k], minutes=mins.get(d), phase=phase,
                           firm=False, title="", conf="Sketch", aim=_aim(p, k), noun=wnoun if k == LONG else "")
            row.update(week_hours=hours, week_phase=phase, week_hard=hard_n, week_long=long_w, week_noun=wnoun,
                       today=d.isoformat() == rx.date)
            out.append(row)
        mon += timedelta(days=7)
    return out


def duration(m: int | None) -> str:
    if not m:
        return ""
    if m < 60:
        return f"{m} min"
    h, r = divmod(m, 60)
    return f"{h} h" if not r else f"{h} h {r:02d}"


def months(days: list[dict]) -> list[dict]:
    """The outlook as month grids (Monday first), one week row at a time, each with its phase and hours."""
    if not days:
        return []
    by = {d["date"]: d for d in days}
    first, last = date.fromisoformat(days[0]["date"]), date.fromisoformat(days[-1]["date"])
    out = []
    y, m = first.year, first.month
    while (y, m) <= (last.year, last.month):
        d1 = date(y, m, 1)
        nxt = date(y + (m == 12), m % 12 + 1, 1)
        mon = d1 - timedelta(days=d1.weekday())
        weeks = []
        while mon < nxt:
            cells, info = [], None
            for i in range(7):
                d = mon + timedelta(days=i)
                row = by.get(d.isoformat()) if d.month == m else None
                if row is not None:
                    info = info or row
                    row = dict(row, day=d.day, dur=duration(row.get("minutes")), wlong=duration(int(round(row["week_long"] / 5) * 5) if row.get("week_long") else None))
                cells.append(row if row is not None else
                             ({"day": d.day, "date": d.isoformat(), "past": True} if d.month == m else None))
            any_day = next((by.get((mon + timedelta(days=i)).isoformat()) for i in range(7)
                            if by.get((mon + timedelta(days=i)).isoformat())), None)
            weeks.append(dict(cells=cells, phase=(any_day or {}).get("week_phase", ""),
                              hours=(any_day or {}).get("week_hours"), shown=any(c and not c.get("past") for c in cells)))
            mon += timedelta(days=7)
        out.append(dict(ym=f"{y}-{m:02d}", title=d1.strftime("%B %Y"), weeks=weeks,
                        race=any(c and c.get("kind") == RACE for w in weeks for c in w["cells"])))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    for x in out:
        x["end"], x["ends_in_race"] = days[-1]["date"], days[-1]["kind"] == RACE
    return out
