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
        conf = "Likely" if k == 1 else "Probable" if k <= 3 else "Sketch"
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
