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
        "stuffy", "cough", "coughing", "achy", "aches", "nauseous", "feel like crap", "feel like shit", "covid"]
POOR = ["junk", "fast food", "pizza", "burger", "fries", "candy", "dessert", "ice cream", "chips", "ate like shit",
        "ate like crap", "ate badly", "ate bad", "ate poorly", "skipped dinner", "skipped lunch", "didn't eat", "barely ate",
        "late dinner", "ate late"]
GOOD = ["healthy", "ate well", "ate clean", "salad", "vegetables", "veggies", "eggs", "rice", "chicken", "fish", "salmon",
        "oatmeal", "fruit", "protein", "good meal", "ate great"]
STRESS = ["stressed", "stress", "stressful", "anxious", "slammed", "long day", "rough day", "fight", "argument", "deadline"]
TIRED = ["tired", "exhausted", "wiped", "drained", "fatigued", "heavy legs", "legs are dead", "sore legs", "legs are sore", "no energy"]
GREAT = ["great", "fresh", "strong", "awesome", "amazing", "energized", "good legs", "felt good", "feel good"]
LATE = ["up late", "late night", "stayed up", "bed late", "went to bed at 1", "went to bed at 12", "midnight"]
# Body parts: pain in the ones running loads is a reason to keep off your feet for a few days.
IMPACT = ["foot", "feet", "heel", "heels", "arch", "arches", "plantar", "toe", "toes", "shin", "shins", "calf", "calves",
          "achilles", "ankle", "ankles", "knee", "knees", "hip", "hips", "it band", "itb"]
OTHER_PARTS = ["back", "lower back", "neck", "shoulder", "shoulders", "wrist", "wrists", "hand", "hands", "hamstring",
               "hamstrings", "quad", "quads", "groin", "glute", "glutes"]
HURT = r"(hurts?|hurting|sore|pain|painful|aches?|aching|tweaked|strained|pulled|injured|tender|niggle|twinge|blister(?:s|ed)?)"
HUNGRY = ["hungry", "starving", "famished", "bonked", "bonk", "hangry", "ravenous", "empty tank"]
SHOES = re.compile(r"\b(shoes?|trainers|sneakers|insoles?)\b")
CAFFEINE = ["over caffeinated", "overcaffeinated", "over-caffeinated", "too much coffee", "too much caffeine", "jittery",
            "wired", "jacked up", "espresso", "energy drink", "pre-workout", "preworkout", "coffees", "red bull", "celsius"]
HEADACHE = ["headache", "head ache", "migraine", "head hurts", "head is pounding", "pounding head"]
BAD_SLEEP = ["slept badly", "slept bad", "slept poorly", "slept like crap", "slept like shit", "bad sleep", "terrible sleep",
             "couldn't sleep", "could not sleep", "can't sleep", "insomnia", "woke up a lot", "woke up all night",
             "kids woke", "baby woke", "up all night", "restless night"]
NERVES = ["anxious", "nervous", "wired and tired", "on edge", "can't switch off", "mind racing"]
TRAVEL = ["flight", "flew", "flying", "jet lag", "jetlag", "jet lagged", "red eye", "red-eye", "long drive", "time zone"]
HEAT = ["hot out", "so hot", "heat", "humid", "sweltering", "dehydrated", "didn't drink", "not enough water"]
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
    pain = []
    for part in sorted(IMPACT + OTHER_PARTS, key=len, reverse=True):
        p_ = re.escape(part)
        if re.search(r"\b" + p_ + r"\b(?:\s+\w+){0,3}?\s+" + HURT + r"\b", t) or \
           re.search(r"\b" + HURT + r"\s+(?:\w+\s+){0,2}" + p_ + r"\b", t):
            if not any(part in x for x in pain):
                pain.append(part)
    if pain and "no pain" not in t and "doesn't hurt" not in t and "don't hurt" not in t:
        tags["pain"] = pain
        if SHOES.search(t):
            tags["shoes"] = True
    if any(re.search(r"\b" + re.escape(w) + r"\b", t) for w in HUNGRY):
        tags["hungry"] = True
    has = lambda ws: any(re.search(r"\b" + re.escape(w) + r"\b", t) for w in ws)
    if has(CAFFEINE) or re.search(r"\b(\d|three|four|five|six)\s+(?:cups of )?coffees?\b", t):
        tags["caffeine"] = True
    if has(HEADACHE):
        tags["headache"] = True
    if has(BAD_SLEEP):
        tags["bad_sleep"] = True
    if has(NERVES):
        tags["stress"] = True
    if has(TRAVEL):
        tags["travel"] = True
    if has(HEAT):
        tags["heat"] = True
    return tags


def impact_pain(tags: dict) -> list[str]:
    """The painful parts that running loads (feet, shins, calves, Achilles, knees, hips)."""
    return [x for x in (tags.get("pain") or []) if x in IMPACT]


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
    for x in tags.get("pain") or []:
        out.append(f"Sore {x}")
    if tags.get("hungry"):
        out.append("Hungry")
    for k, l in (("caffeine", "Lots of caffeine"), ("headache", "Headache"), ("bad_sleep", "Slept badly"),
                 ("travel", "Travel"), ("heat", "Heat")):
        if tags.get(k):
            out.append(l)
    return out


def effect(tags: dict, hrv_low: bool = False) -> tuple[str | None, str]:
    """(cap for tomorrow, what it means), from today's check-ins.
    `hrv_low`: your HRV has been under your normal this week, which makes "tired" count."""
    d = tags.get("drinks") or 0
    notes, cap = [], None
    if tags.get("sick"):
        return "easy", ("Feeling sick changes everything: tomorrow is easy at most, and if your resting HR is up "
                        "in the morning, it's a day off. Training through the start of an illness is how a few days "
                        "off turns into two weeks.")
    if d >= 3 or d < 0 and tags.get("late"):
        cap = "steady"
        notes.append("Alcohol is the single biggest hit to overnight HRV most people take: it raises your heart "
                     "rate all night and cuts deep sleep. Tomorrow is capped at steady unless your HRV comes in normal.")
    elif d:
        notes.append("A drink or two usually shows up as a small HRV dip. Tomorrow stands, but expect the morning "
                     "numbers to read a little low.")
    if tags.get("feel") == "tired":
        if hrv_low:
            cap = cap or "steady"
            notes.append("Tired, on top of a week of HRV under your normal: that's two signals saying the same thing. "
                         "Tomorrow starts as steady, not hard. If your HRV comes back to normal in the morning, "
                         "the hard day is back on.")
        else:
            notes.append("Tired, but your numbers say you're recovering. Tomorrow stands; if you still feel flat in "
                         "the warm-up, make it steady.")
    pain = impact_pain(tags)
    if pain:
        notes.append(f"Sore {' and '.join(pain[:2])}: no running for the next few days. Ride, ski or swim instead, "
                     "same heart rate. If it still hurts after three days, or it's sharp, get it looked at."
                     + (" And it may be the shoes: try another pair before your next run." if tags.get("shoes") else ""))
    elif tags.get("pain"):
        notes.append(f"Sore {tags['pain'][0]}: keep tomorrow's session easy on it, and skip anything that makes it worse.")
    if tags.get("hungry"):
        notes.append("Hungry on a training day means you're under-fuelled. Eat a real meal now: carbs and protein, "
                     "about 0.5 g of carbs per pound of body weight in the next couple of hours. Tomorrow's session "
                     "needs it in the tank.")
    if tags.get("caffeine"):
        notes.append("Lots of caffeine lifts your heart rate 5 to 10 beats for hours and cuts deep sleep. No more after "
                     "2 PM; expect tonight's HRV to read a little low, and today's heart-rate ceiling to arrive sooner.")
    if tags.get("headache") and not tags.get("sick"):
        notes.append("A headache: drink a big glass of water now and eat. If it's still there in the morning with your "
                     "resting heart rate up, tomorrow goes easy.")
    if tags.get("bad_sleep"):
        notes.append("A bad night shows up in tomorrow's numbers. Ceiling reads them in the morning; aim for an early night.")
    if tags.get("travel"):
        notes.append("Travel days are stress your watch doesn't see. Keep anything today easy and drink more than you think.")
    if tags.get("heat"):
        notes.append("Heat pushes heart rate up 5 to 10 beats at the same effort: go by heart rate, not pace or power, "
                     "and drink before you're thirsty.")
    if tags.get("late") and not notes:
        notes.append("A late night is a short night. Tomorrow depends on how much sleep you actually get.")
    if tags.get("food") == "poor" and not tags.get("hungry"):
        notes.append("A poor eating day matters most before a hard session: eat a real meal tonight and before tomorrow's workout.")
    return cap, " ".join(notes)


def hrv_low(wellness, day: str, morning: bool = False) -> bool:
    """This week's HRV under 95% of your 60-day normal (the nights up to `day`).
    morning=True (deciding today's session): also needs last night itself under normal, so a
    tired evening followed by a normal night puts the hard day back on."""
    ws = sorted((w for w in wellness if w.hrv and w.date <= day), key=lambda w: w.date)
    if len(ws) < 21:
        return False
    week = [w.hrv for w in ws[-7:]]
    base = statistics.median([w.hrv for w in ws[-67:-7]])
    if morning and ws[-1].date == day and ws[-1].hrv >= base * 0.97:
        return False
    return statistics.mean(week) < base * 0.95


def rhr_up(wellness, day: str) -> bool:
    """This morning's resting HR 5+ beats over your 30-day median."""
    ws = sorted((w for w in wellness if w.rhr and w.date <= day), key=lambda w: w.date)
    if len(ws) < 14 or ws[-1].date != day:
        return False
    return ws[-1].rhr >= statistics.median([w.rhr for w in ws[-31:-1]]) + 5


def signals(store, day: str, hrv_low: bool = False, rhr_up: bool = False) -> dict:
    """What the journal means for the morning of `day`: last night's check-ins (yesterday's entries)
    and anything logged so far today. Pain in the feet or legs keeps you off running for 3 days."""
    from datetime import date as _d
    d0 = _d.fromisoformat(day)
    out = {"cap": None, "why": "", "ban": set(), "note": ""}
    caps = []
    for back in (1, 0):
        e = load(store, (d0 - timedelta(days=back)).isoformat())
        if not e:
            continue
        tg = e.get("tags") or {}
        c, n = effect(tg, hrv_low)
        if c:
            caps.append((c, n))
        if tg.get("headache") and rhr_up:
            caps.append(("easy", "A headache yesterday and your resting heart rate up this morning: easy today, in case it's the start of something."))
        if back == 1 and tg.get("travel") and hrv_low:
            caps.append(("steady", "A travel day yesterday and your HRV still under normal: no hard work today."))
    order = ["rest", "light", "easy", "steady", "hard"]
    if caps:
        c, n = min(caps, key=lambda x: order.index(x[0]))
        out["cap"], out["why"] = c, n.split(". ")[0] + "."
    for back in range(0, 3):
        e = load(store, (d0 - timedelta(days=back)).isoformat())
        if e and impact_pain(e.get("tags") or {}):
            out["ban"] = {"run", "trail run"}
            out["note"] = (f"You logged a sore {impact_pain(e['tags'])[0]} "
                           f"{'today' if back == 0 else 'yesterday' if back == 1 else 'two days ago'}: no running "
                           "until it's settled.")
            break
    return out


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
        for k in ("sick", "stress", "late", "hungry", "shoes", "caffeine", "headache", "bad_sleep", "travel", "heat"):
            if t.get(k):
                tags[k] = True
        for x in t.get("pain") or []:
            tags.setdefault("pain", [])
            if x not in tags["pain"]:
                tags["pain"].append(x)
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
    for x in t.get("pain") or []:
        pills.append((f"Sore {x}", "bad" if x in IMPACT else "warn"))
    if t.get("hungry"):
        pills.append(("Hungry", "warn"))
    for k, l, tone in (("caffeine", "Lots of caffeine", "warn"), ("headache", "Headache", "warn"),
                       ("bad_sleep", "Slept badly", "warn"), ("travel", "Travel", "plain"), ("heat", "Heat", "warn")):
        if t.get(k):
            pills.append((l, tone))
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
                       ("a late night", lambda t: t.get("late")),
                       ("a lot of caffeine", lambda t: t.get("caffeine")),
                       ("a headache", lambda t: t.get("headache")),
                       ("travel", lambda t: t.get("travel")),
                       ("feeling tired", lambda t: t.get("feel") == "tired")):
        hit = [x for t, x in rows if test(t)]
        rest = [x for t, x in rows if not test(t)]
        if len(hit) >= 3 and len(rest) >= 3:
            diff = (statistics.mean(hit) - statistics.mean(rest)) * 100
            if abs(diff) >= 5:
                out.append(f"After {name}, your HRV the next morning runs {abs(diff):.0f}% "
                           f"{'lower' if diff < 0 else 'higher'} ({len(hit)} nights).")
    return out
