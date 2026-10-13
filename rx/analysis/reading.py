"""What last night actually means: one named reading, said plainly, with the why.

Signals are never read alone. The same HRV number means different things after a
three-hour ride than after a rest day, and HRV down means more when resting HR is
up too. This is the interpretation matrix from the research (HRV4Training's
states, Plews/Buchheit/Kiviniemi/Le Meur), in priority order:

  1. Illness gate     RHR up >=5 (or >=3 two days running) with HRV down
  2. Not absorbing    7-day HRV below your normal range AND RHR up >=3
  3. Trending low     7-day HRV below your normal range
  4. Saturation       7-day HRV well above normal under heavy fatigue (TSB/CTL < -0.25)
  5. Expected dip     last night low, but a big day in the last 2 days
  6. One-off dip      last night low, no training reason (alcohol? heat? stress?)
  7. RHR up alone     RHR >=3 above normal, HRV fine
  8. Sleep            short night or a week of debt caps intensity on its own
  9. Absorbing well   everything in or above your range

HRV is ln(rMSSD). "Normal range" = mean +/- 0.5 SD of the last 60 nights, the
smallest change that matters. Decisions use the 7-day average; one night is a flag.
Everything is compared to this athlete's own history; there are no population norms.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta

REST, LIGHT, EASY, STEADY, HARD = "rest", "light", "easy", "steady", "hard"
ORDER = [REST, LIGHT, EASY, STEADY, HARD]
BASELINE_NIGHTS = 60
MIN_NIGHTS = 14


@dataclass
class Evidence:
    name: str
    line: str        # the numbers, against your own normal
    tone: str        # good | ok | bad | none


@dataclass
class Reading:
    key: str
    title: str                 # plain, short: "Not absorbing the load"
    tone: str                  # good | ok | bad | none
    says: str                  # what it means, in one or two sentences
    action: str                # what we're doing about it today
    cap: str                   # the most today can be
    evidence: list[Evidence] = field(default_factory=list)
    ask: str = ""              # the question only you can answer (drinks? sick?)
    confidence: str = ""


def cap_verdict(verdict: str, cap: str) -> str:
    return ORDER[min(ORDER.index(verdict), ORDER.index(cap))]


def _pct_below(value: float, history: list[float]) -> float:
    """Share of past values below this one, 0..100."""
    if not history:
        return 50.0
    return 100.0 * sum(1 for v in history if v < value) / len(history)


def _where(value: float, history: list[float], what: str, higher_is_good: bool) -> str:
    """'Lower than 84% of your nights this year.' Only said when it's notable."""
    if len(history) < 30:
        return ""
    p = _pct_below(value, history)
    if p >= 75:
        return f"Higher than {p:.0f}% of your {what} this year."
    if p <= 25:
        return f"Lower than {100 - p:.0f}% of your {what} this year."
    return f"A typical {what[:-1]} for you."


def _hm(hours: float) -> str:
    h = int(hours)
    m = int(round((hours - h) * 60))
    if m == 60:
        h, m = h + 1, 0
    return f"{h}h {m:02d}m"


def read(wellness, daily_load: dict[str, float], load_days, day: str, response=None) -> Reading:
    d0 = date.fromisoformat(day)
    by = {w.date: w for w in wellness}
    iso = lambda n: (d0 - timedelta(days=n)).isoformat()
    today = by.get(day)
    past = [w for w in wellness if w.date < day]
    year = [w for w in past if w.date >= iso(365)]
    base = past[-BASELINE_NIGHTS:]
    ev: list[Evidence] = []

    # ---- HRV -------------------------------------------------------------
    ln_base = [math.log(w.hrv) for w in base if w.hrv and w.hrv > 0]
    hrv_today = today.hrv if today and today.hrv else None
    last7 = [math.log(by[iso(n)].hrv) for n in range(7) if iso(n) in by and by[iso(n)].hrv]
    have_hrv = len(ln_base) >= MIN_NIGHTS and hrv_today is not None and len(last7) >= 3
    z_t = z_7 = 0.0
    hrv_low7 = hrv_high7 = hrv_well_high = False
    if have_hrv:
        mu, sd = statistics.mean(ln_base), max(statistics.pstdev(ln_base), 0.04)
        lo, hi = math.exp(mu - 0.5 * sd), math.exp(mu + 0.5 * sd)
        h7 = statistics.mean(last7)
        z_t, z_7 = (math.log(hrv_today) - mu) / sd, (h7 - mu) / sd
        hrv_low7, hrv_high7, hrv_well_high = z_7 < -0.5, z_7 > 0.5, z_7 > 1.0
        tone = "bad" if hrv_low7 else "good" if z_7 >= -0.5 and z_t > -1 else "ok"
        where = _where(hrv_today, [w.hrv for w in year if w.hrv], "nights", True)
        ev.append(Evidence("HRV", f"{hrv_today:.0f} ms last night; {math.exp(h7):.0f} ms 7-day average. "
                                  f"Your normal range is {lo:.0f}–{hi:.0f} ms. {where}".strip(), tone))

    # ---- Resting HR --------------------------------------------------------
    rhr_base = [w.rhr for w in base if w.rhr]
    rhr_d = rhr_prev = None
    if len(rhr_base) >= MIN_NIGHTS and today and today.rhr:
        norm = statistics.median(rhr_base)
        rhr_d = today.rhr - norm
        y = by.get(iso(1))
        rhr_prev = (y.rhr - norm) if y and y.rhr else None
        tone = "bad" if rhr_d >= 3 else "good" if rhr_d <= 0 else "none"
        where = _where(today.rhr, [w.rhr for w in year if w.rhr], "mornings", False)
        diff = ("right on your normal" if abs(rhr_d) < 1 else
                f"{abs(rhr_d):.0f} {'above' if rhr_d > 0 else 'below'} your normal of {norm:.0f}")
        ev.append(Evidence("Resting HR", f"{today.rhr:.0f} bpm, {diff}. {where}".strip(), tone))

    # ---- Sleep -------------------------------------------------------------
    sl_base = [w.sleep_secs / 3600 for w in past[-30:] if w.sleep_secs]
    sleep_h = today.sleep_secs / 3600 if today and today.sleep_secs else None
    short_night = debt = False
    if sleep_h is not None and len(sl_base) >= 7:
        usual = statistics.median(sl_base)
        week = [by[iso(n)].sleep_secs / 3600 for n in range(7) if iso(n) in by and by[iso(n)].sleep_secs]
        deficit = sum(max(0.0, usual - h) for h in week)
        short_night = sleep_h < 6.0 or sleep_h < usual - 1.5
        debt = False   # running sleep debt is handled by the sleep bank (analysis/sleep.py)
        gap = sleep_h - usual
        line = f"{_hm(sleep_h)} last night; your usual is {_hm(usual)}"
        line += "." if abs(gap) < 0.25 else f" ({'+' if gap > 0 else '−'}{_hm(abs(gap))})."
        if deficit >= 2:
            line += f" You're {_hm(deficit)} short over the last 7 nights."
        if today.sleep_score:
            line += f" Sleep score {today.sleep_score:.0f}."
        ev.append(Evidence("Sleep", line, "bad" if short_night or debt else "ok" if deficit >= 2 else "good"))

    # ---- Training context ----------------------------------------------------
    last28 = [daily_load.get(iso(n), 0.0) for n in range(1, 29)]
    avg = statistics.mean(last28) if any(last28) else 0.0
    y1, y2 = daily_load.get(iso(1), 0.0), daily_load.get(iso(2), 0.0)
    big1 = avg > 0 and y1 > 1.5 * avg
    big2 = avg > 0 and y2 > 1.5 * avg
    ld = load_days[-1] if load_days else None
    heavy = bool(ld and ld.ctl > 5 and ld.tsb / ld.ctl < -0.25)
    if avg:
        if big1:
            ctx = f"Yesterday was a big day: {y1 / avg:.1f}× your daily average."
        elif big2:
            ctx = f"Two days ago was a big day: {y2 / avg:.1f}× your daily average."
        elif y1 == 0:
            ctx = "Yesterday was a rest day."
        else:
            ctx = f"Yesterday was {'a light' if y1 < 0.75 * avg else 'a normal'} day ({y1 / avg:.1f}× your average)."
        if heavy:
            ctx += " You're carrying a lot of fatigue from the last week."
        ev.append(Evidence("Training", ctx, "ok" if big1 or big2 or heavy else "none"))

    # ---- Stress from outside training ------------------------------------------
    # Your own history says how much yesterday's load should move HRV. A shortfall
    # beyond that is stress training doesn't explain (sleep, alcohol, work, illness).
    outside = 0.0
    if have_hrv and response is not None and getattr(response, "fitted", False) and len(load_days) >= 2 \
            and load_days[-2].ctl > 5:
        rel = load_days[-1].load / load_days[-2].ctl
        expected = response.intercept + response.slope * rel
        outside = z_t - expected
        if outside < -1.0:
            ev.append(Evidence("Outside training",
                               f"Yesterday's training explains only part of last night's HRV: you're "
                               f"{abs(outside):.1f} SD below what that load usually does to you. Something else "
                               f"is adding stress: short sleep, drinks, work, travel, or a bug coming on.", "bad"))
        elif outside > 0.75 and z_t < -0.5:
            ev.append(Evidence("Outside training",
                               "The dip is about what yesterday's training normally does to you. Nothing else "
                               "is piling on.", "good"))

    two_short = sum(1 for n in range(2) if iso(n) in by and by[iso(n)].sleep_secs
                    and by[iso(n)].sleep_secs < 6 * 3600) == 2

    nights = len(ln_base)
    conf = ("Low confidence: only %d nights of history so far." % nights if nights < 28 else
            "Based on your last %d nights." % nights)

    def R(key, title, tone, says, action, cap, ask=""):
        r = Reading(key, title, tone, says, action, cap, ev, ask, conf)
        # Sleep caps on its own, whatever the HRV says.
        if (short_night or debt) and ORDER.index(cap) > ORDER.index(STEADY):
            r.cap = STEADY
            r.action += (" Short sleep costs the most on hard efforts, so no intervals today."
                         if short_night else " You're in sleep debt, so nothing hard until you've caught up.")
        return r

    # ---- The matrix, strongest signal first -----------------------------------
    if not have_hrv and rhr_d is None:
        return R("unknown", "No overnight data", "none",
                 "Nothing came through from last night's watch, so there's nothing to read.",
                 "We're keeping today easy until there's data.", EASY)

    if rhr_d is not None and two_short and rhr_d >= 5:
        return R("illness", "Run down", "bad",
                 "Two short nights and resting HR is up 5+ beats. That's when people catch colds: short "
                 "sleepers get sick about four times as often when exposed. Your body needs sleep, not training.",
                 "Walk at most, nothing long in the cold, and in bed early tonight. 48 hours of this.", LIGHT,
                 "Scratchy throat or achy? Then it's a full day off.")

    if rhr_d is not None and (rhr_d >= 5 or (rhr_d >= 3 and (rhr_prev or 0) >= 3)) and (z_t < -0.5 or hrv_low7):
        return R("illness", "Something's off", "bad",
                 "Resting HR is up and HRV is down at the same time. That's the classic sign your body is "
                 "fighting something: a bug coming on, a big stress hit, or a lot of alcohol.",
                 "Walk at most. No intensity until resting HR is back within 2 beats of normal.", LIGHT,
                 "Sore throat, achy, or just flat? If anything's going on below the neck, take the day fully off.")

    if hrv_low7 and rhr_d is not None and rhr_d >= 3:
        return R("not_absorbing", "Not absorbing the load", "bad",
                 "Your HRV has been below your normal range all week and resting HR is up. Together, that "
                 "means the training isn't sinking in. Fatigue is piling up faster than you're clearing it.",
                 "Easy only. If this lasts 3 more days, we cut the week's volume by a third.", EASY)

    if hrv_low7:
        return R("trending_low", "HRV trending low", "ok",
                 "One low night is noise; a whole week below your normal range is a signal. Resting HR is "
                 "holding, so it's early. This is the time to back off, not after it gets worse.",
                 "Easy today. Hard sessions wait until your 7-day HRV is back in range.", EASY)

    if hrv_well_high and heavy and (rhr_d is None or rhr_d <= 0):
        return R("saturation", "High HRV, heavy legs?", "ok",
                 "HRV is well above your normal while you're carrying a lot of fatigue. In people who train "
                 "as much as you, that can be the body turning down rather than fitness going up "
                 "(it's called parasympathetic saturation). It's not automatically good news.",
                 "Steady at most. Don't add load until the fatigue comes off.", STEADY,
                 "Legs heavy, or HR slow to climb on the hills? Then this is fatigue, not freshness.")

    if have_hrv and z_t < -1.0 and (big1 or big2):
        return R("expected_dip", "Normal dip after a big day", "ok",
                 "HRV dropped last night, and that's expected after a day that size. Your 7-day average is "
                 "still in your normal range, so you're coping. The dip should be gone within 48 hours.",
                 "Easy today and it clears. If it's still low two mornings from now, we extend the easy days.", EASY)

    if have_hrv and z_t < -1.0:
        return R("one_off_dip", "One-off dip", "ok",
                 "HRV dropped last night with no training reason behind it. Your week is still in range, "
                 "so it's probably one thing: drinks, a late heavy dinner, a hot room, or a stressful day.",
                 "Train, but cap it at steady. Use the warm-up as a check: if HR runs high for the effort, keep it easy.",
                 STEADY, "Any of those last night? If yes, ignore this dip. If no, take it easier today.")

    if rhr_d is not None and rhr_d >= 3:
        return R("rhr_up", "Resting HR is up", "ok",
                 f"Resting HR is {rhr_d:.0f} above normal but HRV is fine. Usually heat, dehydration, a late "
                 f"meal or drinks. Sometimes it's the first sign of a bug.",
                 "Steady at most today. We'll check again tomorrow.", STEADY,
                 "Hot night, drinks, or feeling run down?")

    if short_night or debt:
        return R("short_sleep", "Short on sleep", "ok",
                 "Your HRV and resting HR say you've recovered from the training. Sleep is the weak link: "
                 "short sleep hits hard, high-intensity efforts first and makes everything feel harder.",
                 "Aerobic work is fine.", HARD)

    if hrv_high7 and (rhr_d is None or rhr_d <= 0):
        return R("absorbing", "Absorbing it well", "good",
                 "HRV is above your normal range and resting HR is at or below normal. That's what it looks "
                 "like when training is turning into fitness.", "Good day to do the work.", HARD)

    return R("coping", "Recovered", "good",
             "HRV is in your normal range and resting HR is where it should be. You're recovering from "
             "what you're doing.", "Train as planned.", HARD)
