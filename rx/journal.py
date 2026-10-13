"""The journal: a line a day in your own words, and what it does to you.

You type how you feel, what you ate, what you drank, whether you're getting sick. Ceiling reads
it (plain rules, so it's predictable and private), tags it, and:
  - adjusts tomorrow when it should (drinks, sickness),
  - learns your own patterns: what the next morning's HRV looked like after nights like this one.

Everything is kept in your own tables. Nothing leaves Ceiling.
"""
from __future__ import annotations

import json
import re
import statistics
from datetime import date, timedelta

NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
       "seven": 7, "eight": 8, "a couple": 2, "couple": 2, "a few": 3, "few": 3, "several": 4}
DRINK = r"(beers?|ipas?|lagers?|pints?|wines?|glass(?:es)? of wine|reds?|whites?|gin(?: and tonics?| & tonics?|s)?|g&ts?|" \
        r"whiske?ys?|bourbons?|scotch(?:es)?|tequilas?|margaritas?|vodkas?|cocktails?|martinis?|shots?|drinks?|seltzers?|old fashioneds?|negronis?)"
SICK = ["sick", "sore throat", "fever", "chills", "congested", "congestion", "cold", "flu", "run down", "rundown",
        "stuffy", "cough", "coughing", "achy", "aches", "headache", "nauseous", "feel like crap", "feel like shit", "covid"]
POOR = ["junk", "fast food", "pizza", "burger", "fries", "candy", "dessert", "ice cream", "chips", "ate like shit",
        "ate like crap", "ate badly", "ate bad", "ate poorly", "skipped dinner", "skipped lunch", "didn't eat", "barely ate",
        "late dinner", "ate late"]
GOOD = ["healthy", "ate well", "ate clean", "salad", "vegetables", "veggies", "eggs", "rice", "chicken", "fish", "salmon",
        "oatmeal", "fruit", "protein", "good meal", "ate great"]
STRESS = ["stressed", "stress", "stressful", "anxious", "slammed", "long day", "rough day", "fight", "argument", "deadline"]
TIRED = ["tired", "exhausted", "wiped", "drained", "fatigued", "heavy legs", "legs are dead", "sore legs", "legs are sore", "no energy"]
GREAT = ["great", "fresh", "strong", "awesome", "amazing", "energized", "good legs", "felt good", "feel good"]
LATE = ["up late", "late night", "stayed up", "bed late", "went to bed at 1", "went to bed at 12", "midnight"]
DRANK = re.compile(r"\b(drank|drinking|drunk|hungover|hangover|booze|boozy|wine|beer)\b")


def parse(text: str) -> dict:
    t = " " + text.lower().replace("’", "'") + " "
    tags: dict = {}
    drinks = 0
    for m in re.finditer(r"\b(\d+|" + "|".join(sorted(NUM, key=len, reverse=True)) + r")\s+(?:\w+\s+){0,2}?" + DRINK + r"\b", t):
        n = m.group(1)
        drinks += int(n) if n.isdigit() else NUM.get(n, 1)
    if not drinks and (DRANK.search(t) or re.search(r"\b" + DRINK + r"\b", t)):
        drinks = -1                                  # drank, amount unknown
    if drinks:
        tags["drinks"] = drinks
    if any(re.search(r"\b" + re.escape(w) + r"\b", t) for w in SICK) and "not sick" not in t:
        tags["sick"] = True
    poor, good = any(w in t for w in POOR), any(w in t for w in GOOD)
    if poor and not good:
        tags["food"] = "poor"
    elif good and not poor:
        tags["food"] = "good"
    if any(w in t for w in STRESS):
        tags["stress"] = True
    if any(re.search(r"\b" + re.escape(w) + r"\b", t) for w in TIRED):
        tags["feel"] = "tired"
    elif any(w in t for w in GREAT):
        tags["feel"] = "good"
    if any(w in t for w in LATE):
        tags["late"] = True
    return tags


def label(tags: dict) -> list[str]:
    out = []
    d = tags.get("drinks")
    if d:
        out.append("Drinks" if d < 0 else "1 or 2 drinks" if d == 2 and tags.get("_tap_drinks") else
                   f"{d} drink{'s' if d != 1 else ''}")
    if tags.get("sick"):
        out.append("Feeling sick")
    if tags.get("food") == "poor":
        out.append("Ate poorly")
    elif tags.get("food") == "good":
        out.append("Ate well")
    if tags.get("stress"):
        out.append("Stressed")
    if tags.get("feel") == "tired":
        out.append("Tired")
    elif tags.get("feel") == "good":
        out.append("Energized")
    if tags.get("late"):
        out.append("Late night")
    return out


def effect(tags: dict) -> tuple[str | None, str]:
    """(cap for tomorrow, what it means), from tonight's entry alone."""
    d = tags.get("drinks") or 0
    if tags.get("sick"):
        return "easy", ("Feeling sick changes everything: tomorrow is easy at most, and if your resting HR is up "
                        "in the morning, it's a day off. Training through the start of an illness is how a few days "
                        "off turns into two weeks.")
    if d >= 3 or d < 0 and tags.get("late"):
        return "steady", ("Alcohol is the single biggest hit to overnight HRV most people take: it raises your heart "
                          "rate all night and cuts deep sleep. Tomorrow is capped at steady unless your HRV "
                          "comes in normal.")
    if d:
        return None, ("A drink or two usually shows up as a small HRV dip. Tomorrow stands, but expect the morning "
                      "numbers to read a little low.")
    if tags.get("late"):
        return None, "A late night is a short night. Tomorrow depends on how much sleep you actually get."
    if tags.get("food") == "poor":
        return None, "A poor eating day matters most before a hard session: eat a real meal tonight and before tomorrow's workout."
    return None, ""


def save(store, day: str, text: str, taps: dict | None = None, at: str = "") -> dict:
    """Add a check-in. Every check-in is kept with its time, so "tired" at 7 AM and
    "energized" at 2 PM both count; the day's tags are built from all of them, the latest
    feeling winning, drinks adding up to the most reported, food the latest word."""
    prev = load(store, day) or {}
    log = prev.get("log") or ([{"at": "", "text": prev.get("text", ""), "taps": prev.get("taps", {})}]
                              if prev.get("text") or prev.get("taps") else [])
    taps = {k: v for k, v in (taps or {}).items() if v}
    if text or taps:
        log.append({"at": at, "text": (text or "").strip()[:500], "taps": taps})
    tags: dict = {}
    for e in log:                                     # oldest first: later check-ins win
        t = parse(e.get("text") or "")
        tp = e.get("taps") or {}
        for k in ("sick", "stress", "late"):
            if t.get(k):
                tags[k] = True
        if t.get("drinks"):
            tags["drinks"] = max(tags.get("drinks") or 0, t["drinks"]) if t["drinks"] > 0 else (tags.get("drinks") or -1)
        if t.get("food"):
            tags["food"] = t["food"]
        if t.get("feel"):
            tags["feel"] = t["feel"]
        f = tp.get("feel")
        if f == "sick":
            tags["sick"] = True
        elif f in ("good", "tired"):
            tags["feel"] = f
        elif f == "normal":
            tags.pop("feel", None)
        if tp.get("drinks") in ("0", "2", "3"):
            n = int(tp["drinks"])
            if n == 0:
                tags.pop("drinks", None)
            else:
                tags["drinks"] = max(n, tags.get("drinks") or 0)
                tags["_tap_drinks"] = True
        if tp.get("food") in ("good", "poor"):
            tags["food"] = tp["food"]
        elif tp.get("food") == "ok":
            tags.pop("food", None)
    latest = {}
    for e in log:
        latest.update(e.get("taps") or {})
    store.set_meta(f"journal:{day}", json.dumps({"text": "", "tags": tags, "taps": latest, "log": log[-40:]}))
    return tags


TAP = {"feel": {"good": ("Energized", "good"), "normal": ("Normal", "plain"), "tired": ("Tired", "warn"),
                "sick": ("Sick", "bad")},
       "drinks": {"0": ("No drinks", "drink"), "2": ("1 or 2 drinks", "drink"), "3": ("3 or more drinks", "drink")},
       "food": {"good": ("Ate well", "good"), "ok": ("Ate OK", "food"), "poor": ("Ate poorly", "food")}}


def view(e: dict) -> dict:
    """One check-in as the timeline shows it: its time (12-hour, local; blank on old entries),
    the pills tapped, then anything Ceiling read from the text that a tap didn't already say."""
    taps = e.get("taps") or {}
    pills = [TAP[k][taps[k]] for k in ("feel", "drinks", "food") if taps.get(k) in TAP[k]]
    t = parse(e.get("text") or "")
    d = t.get("drinks")
    if d and "drinks" not in taps:
        pills.append(("Drinks" if d < 0 else f"{d} drink{'s' if d != 1 else ''}", "drink"))
    if t.get("sick") and taps.get("feel") != "sick":
        pills.append(("Sick", "bad"))
    if t.get("food") and "food" not in taps:
        pills.append(TAP["food"][t["food"]])
    if t.get("feel") and "feel" not in taps and not t.get("sick"):
        pills.append(TAP["feel"]["good" if t["feel"] == "good" else "tired"])
    if t.get("stress"):
        pills.append(("Stressed", "warn"))
    if t.get("late"):
        pills.append(("Late night", "plain"))
    return {"at": e.get("at") or "", "pills": [{"label": l, "tone": c} for l, c in pills],
            "text": (e.get("text") or "").strip()}


def timeline(entry: dict | None) -> list[dict]:
    """Today's check-ins, oldest first."""
    return [view(e) for e in ((entry or {}).get("log") or []) if e.get("text") or e.get("taps")]


def load(store, day: str) -> dict | None:
    v = store.get_meta(f"journal:{day}")
    try:
        return json.loads(v) if v else None
    except ValueError:
        return None


def all_entries(store) -> dict[str, dict]:
    out = {}
    for r in store.db.execute("SELECT key, value FROM meta WHERE key LIKE 'journal:%'"):
        try:
            out[r["key"][8:]] = json.loads(r["value"])
        except Exception:
            pass
    return out


def patterns(store, wellness) -> list[str]:
    """Your own numbers: the morning after nights like this, against your other mornings.
    Only said once there are at least 3 nights of a kind, and only if the gap is real (>5%)."""
    entries = all_entries(store)
    if len(entries) < 3:
        return []
    hrv = {w.date: w.hrv for w in wellness if w.hrv}
    rows = []
    for d, e in entries.items():
        nxt = (date.fromisoformat(d) + timedelta(days=1)).isoformat()
        base = [v for k, v in hrv.items() if k < nxt][-30:]
        if nxt in hrv and len(base) >= 10:
            rows.append((e.get("tags", {}), hrv[nxt] / statistics.median(base) - 1))
    out = []
    for name, test in (("drinking", lambda t: (t.get("drinks") or 0) != 0),
                       ("3 or more drinks", lambda t: (t.get("drinks") or 0) >= 3),
                       ("eating poorly", lambda t: t.get("food") == "poor"),
                       ("eating well", lambda t: t.get("food") == "good"),
                       ("a stressful day", lambda t: t.get("stress")),
                       ("a late night", lambda t: t.get("late"))):
        hit = [x for t, x in rows if test(t)]
        rest = [x for t, x in rows if not test(t)]
        if len(hit) >= 3 and len(rest) >= 3:
            diff = (statistics.mean(hit) - statistics.mean(rest)) * 100
            if abs(diff) >= 5:
                out.append(f"After {name}, your HRV the next morning runs {abs(diff):.0f}% "
                           f"{'lower' if diff < 0 else 'higher'} ({len(hit)} nights).")
    return out
