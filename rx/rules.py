"""Your setup and your standing rules.

Setup is what onboarding asks once (and you can change any time in Profile): what you do, what
you're working toward, anything holding you back, how many days a week, when you wake.

A rule is something true for a while, not just today: "healing from a crash, no lifting or
running for four weeks", "no Wednesdays", "knee's sore". It comes from setup or from anything you
tell your coach, is shown on Today while it lasts, and steers every day until it ends (on its date,
or when you end it). Rule-based and private, like the plan parser: the same sentence always does
the same thing."""
from __future__ import annotations

import json
import re
import secrets
from datetime import date, timedelta

SPORTS = {"ride": "Ride", "run": "Run", "ski": "Ski", "lift": "Lift"}
GOALS = {"direct": "Just tell me what to do each day", "fitter": "Get fitter", "event": "Train for an event",
         "weight": "Lose weight", "stronger": "Get stronger"}
KINDS = {"ride": {"mountain bike", "road ride", "gravel ride", "trainer ride"},
         "run": {"run", "trail run"}, "ski": {"ski tour", "nordic ski", "resort skiing"}}
SAY = {"ride": "riding", "run": "running", "ski": "skiing", "lift": "lifting"}

_N = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "eight": 8, "ten": 10, "twelve": 12}
_MON = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


# ------------------------------------------------------------------ setup

def setup(store) -> dict | None:
    try:
        return json.loads(store.get_meta("setup") or "null")
    except ValueError:
        return None


def save_setup(store, data: dict) -> dict:
    old = setup(store) or {}
    s = {**old, **{k: v for k, v in data.items() if v is not None}, "done": True}
    store.set_meta("setup", json.dumps(s))
    return s


def guess(store, p) -> dict:
    """What Ceiling already knows, to pre-fill setup for someone who's been using it."""
    from .prescribe import engine as eng
    sports = []
    cut = (date.today() - timedelta(days=120)).isoformat()
    kinds = {eng.SUBTYPE.get(a.raw_type or "") for a in p.activities if a.date >= cut}
    for s, ks in KINDS.items():
        if kinds & ks:
            sports.append(s)
    try:
        w = float(store.get_meta("wake_hour") or 7.0)
    except ValueError:
        w = 7.0
    return {"sports": sports or ["ride"], "goal": "direct", "limits": "", "days": 5,
            "wake": f"{int(w):02d}:{int(round((w - int(w)) * 60)) % 60:02d}"}


# ------------------------------------------------------------------ rules

def all_rules(store) -> list[dict]:
    try:
        return json.loads(store.get_meta("rules") or "[]")
    except ValueError:
        return []


def active(store, day: str) -> list[dict]:
    return [r for r in all_rules(store) if not r.get("ended") and r.get("start", "") <= day
            and (not r.get("until") or day <= r["until"])]


def _save(store, rules: list[dict]) -> None:
    store.set_meta("rules", json.dumps(rules[-50:]))


def end(store, rid: str, day: str) -> None:
    rs = all_rules(store)
    for r in rs:
        if r["id"] == rid:
            r["ended"] = day
    _save(store, rs)


def add(store, r: dict) -> dict:
    rs = all_rules(store)
    r["id"] = r.get("id") or secrets.token_hex(4)
    rs.append(r)
    _save(store, rs)
    return r


def _until(t: str, today: date) -> str | None:
    m = re.search(r"\b(?:for|next|another)\s+(\d+|a|an|one|two|three|four|five|six|eight|ten|twelve)\s+(day|week|month)s?\b", t)
    if m:
        n = int(m.group(1)) if m.group(1).isdigit() else _N[m.group(1)]
        return (today + timedelta(days=n * {"day": 1, "week": 7, "month": 30}[m.group(2)])).isoformat()
    m = re.search(r"\buntil\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*(\d{1,2})", t)
    if m:
        mon, d = _MON[m.group(1)], int(m.group(2))
        y = today.year + (1 if (mon, d) < (today.month, today.day) else 0)
        try:
            return date(y, mon, d).isoformat()
        except ValueError:
            return None
    return None


INJURY = r"\b(crash|crashed|broke|broken|fracture|concussion|surgery|operation|injur(?:y|ed)|healing|recovering from|rehab|stitches|sprain|torn)\b"


def parse(text: str, today: date) -> dict | None:
    """A standing rule from what you said, or None if it's just about today."""
    t = (text or "").lower()
    bans = set()
    words = (("lift", r"lift(?:ing|s)?|weights|strength|gym"), ("run", r"run(?:ning|s)?"),
             ("ride", r"rid(?:e|ing)|bik(?:e|ing)|cycling"), ("ski", r"ski(?:ing)?"))
    for m in re.finditer(r"\b(?:no|not|can'?t|cannot|won'?t|stop|avoid|without)\b([^.;:!?]{0,48})", t):
        seg = m.group(1).split(" but ")[0]
        for s, w in words:
            if re.search(rf"\b(?:{w})\b", seg):
                bans.add(s)
    for s, w in words:
        if re.search(rf"\b(?:{w})\s+is\s+out\b", t):
            bans.add(s)
    injury = re.search(INJURY, t)
    until = _until(t, today)
    travel = bool(re.search(r"\b(travel(?:l?ing)?|on the road|on a trip|business trip|flying|away from home|in hotels?)\b", t))
    if travel and not until:
        until = today.isoformat()
    if not (bans or injury or travel or until and re.search(r"\b(no|not|can'?t|easy)\b", t)):
        return None
    cap = "easy" if injury and re.search(r"concussion|surgery|operation|broke|broken|fracture|crash", t) else None
    label = text.strip().rstrip(".")
    label = label[:1].upper() + label[1:]
    return {"text": label[:140], "bans": sorted(bans), "cap": cap, "start": today.isoformat(), "until": until,
            "injury": bool(injury), "travel": travel}


def describe(r: dict) -> str:
    """'No lifting or running, easy days only, until Nov 11.'"""
    bits = []
    if r.get("bans"):
        b = [SAY[x] for x in r["bans"] if x in SAY]
        bits.append("no " + (", ".join(b[:-1]) + " or " + b[-1] if len(b) > 1 else b[0]))
    if r.get("cap"):
        bits.append(f"{r['cap']} days at most")
    if r.get("travel"):
        bits.insert(0, "travel days: steps and short sessions")
    s = ", ".join(bits) or "taken into account"
    s += "," if len(bits) > 1 else ""
    if r.get("until"):
        s += " until " + date.fromisoformat(r["until"]).strftime("%b %-d")
    else:
        s += " until you end it"
    return s[:1].upper() + s[1:] + "."


def ended_by(text: str) -> bool:
    return bool(re.search(r"\b(back to normal|all healed|healed up|cleared to|good to go|fully recovered|no longer injured)\b",
                          (text or "").lower()))


def merge(js: dict | None, store, day: str) -> dict:
    """Fold setup and standing rules into the day's journal signals the engine already reads:
    bans (kinds of session), a cap on the day, and a note."""
    from .prescribe import engine as eng
    js = dict(js or {"cap": None, "why": "", "ban": set(), "note": ""})
    ban = set(js.get("ban") or ())
    notes = []
    st = setup(store) or {}
    if st.get("sports"):
        for s, ks in KINDS.items():
            if s not in st["sports"]:
                ban |= ks
    js["nolift"] = bool(st.get("sports")) and "lift" not in st["sports"]
    order = ["rest", "light", "easy", "steady", "hard"]
    for r in active(store, day):
        for s in r.get("bans") or []:
            ban |= KINDS.get(s, set())
            if s == "lift":
                js["nolift"] = True
        if r.get("cap") and (not js.get("rule_cap") or order.index(r["cap"]) < order.index(js["rule_cap"])):
            js["rule_cap"], js["rule_until"] = r["cap"], r.get("until")
        if r.get("cap") and (not js.get("cap") or order.index(r["cap"]) < order.index(js["cap"])):
            js["cap"], js["why"] = r["cap"], f"{r['text']}: {describe(r).lower()}"
        if r.get("travel"):
            js["travel"] = r
        notes.append(f"{r['text']}: {describe(r).lower()}")
    js["ban"] = ban
    if notes and not js.get("note"):
        js["note"] = notes[0]
    return js


def lapsed_lift(store, day: str) -> dict | None:
    """A break from lifting that ended in the last ten days and hasn't been answered: time to ask
    whether you're ready to start again."""
    if any("lift" in (r.get("bans") or []) for r in active(store, day)):
        return None
    d = date.fromisoformat(day)
    for r in reversed(all_rules(store)):
        if "lift" not in (r.get("bans") or []) or r.get("asked") or r.get("ended"):
            continue
        if r.get("until") and r["until"] < day and (d - date.fromisoformat(r["until"])).days <= 10:
            return r
    return None


def answer(store, rid: str, again: bool, day: str) -> None:
    """'Start lifting again' closes the break; 'not yet' gives it another week."""
    rs = all_rules(store)
    for r in rs:
        if r["id"] == rid:
            if again:
                r["asked"] = day
                if r.get("until") and r["until"] >= day:
                    r["ended"] = day
            else:
                r["until"] = (date.fromisoformat(day) + timedelta(days=7)).isoformat()
                r.pop("ended", None)
    _save(store, rs)


def steps(store, day: str) -> tuple[int | None, int]:
    """(steps so far today, your usual day), from the watch's daily steps."""
    since = (date.fromisoformat(day) - timedelta(days=30)).isoformat()
    today_n, hist = None, []
    try:
        for r in store.wellness_raw(since):
            raw = json.loads(r["raw"]) if r["raw"] else {}
            n = raw.get("steps")
            if not n:
                continue
            if r["date"] == day:
                today_n = int(n)
            elif r["date"] < day:
                hist.append(int(n))
    except Exception:
        pass
    usual = sorted(hist)[len(hist) // 2] if len(hist) >= 5 else 8000
    return today_n, usual
