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
        if days and sum(x.isoformat() in trained for x in days) / len(days) < 0.25:
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


def _noun(p: Profile, v: str, d: str) -> str:
    try:
        sport, kind, _, _ = eng.choose_activity(v, p, d)
    except Exception:
        return "session"
    return eng.NOUN.get(kind or "", None) or {eng.BIKE: "ride", eng.RUN: "run", eng.SKI: "ski tour", WALK: "walk"}.get(sport, "session")


def _title(p: Profile, v: str, d: str, rx: Rx | None = None) -> str:
    if rx is not None:
        return rx.what.rstrip(".") if rx.what else eng.HEADLINE[v]
    z = p.zones
    if v == REST:
        return "Day off"
    if v == LIGHT:
        return "A walk, 30 to 45 minutes"
    n = _noun(p, v, d)
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
    state = la.step(p.yesterday, today_load, rx.date)
    loads = [p.yesterday.load, today_load]
    hard_days = [rx.date] if rx.verdict == HARD else []
    d0 = date.fromisoformat(rx.date)
    for k in range(1, n):
        d = (d0 + timedelta(days=k)).isoformat()
        v, _ = eng.decide(eng.Inputs(good, state, loads[-2:]))
        reason = ""
        recent_hard = [h for h in hard_days if (date.fromisoformat(d) - date.fromisoformat(h)).days < 7]
        if v == HARD and len(recent_hard) >= 2:
            v, reason = STEADY, "Two hard days already this week."
        if (d0 + timedelta(days=k)).weekday() in rest_wd and v != REST:
            v, reason = REST, f"You rarely train on {(d0 + timedelta(days=k)).strftime('%A')}s."
        detail, load = eng._session(p, v, d)
        if v == HARD:
            hard_days.append(d)
        conf = "Likely" if k == 1 else "Probable" if k <= 3 else "Sketch"
        detail = detail[:1].upper() + detail[1:] + "."
        days.append(PlanDay(d, v, _title(p, v, d), detail, conf, reason))
        state = la.step(state, load, d)
        loads.append(load)
    note = (f"Planned {n} days out. {why} It assumes you sleep normally and do what's planned; "
            f"every morning it's rebuilt from your real sleep, HRV and training.")
    return Plan(days, n, note)


def ics(plan: Plan, url: str = "https://www.ceiling.app/plan") -> str:
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
