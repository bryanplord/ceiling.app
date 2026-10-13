"""Overnight breathing rate and resting heart rate together: the early warning.

Breathing rate during sleep is one of the steadiest numbers the body has: for most people it
moves less than half a breath per minute night to night. A rise of a full breath or more,
especially with resting HR up at the same time, is one of the earliest signs of illness,
often a day or two before you feel anything (Whoop and Oura both lean on it; it showed up
ahead of symptoms in COVID-era wearable studies). Training load alone rarely moves it.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass


@dataclass
class Breathing:
    status: str = "unknown"      # normal | up | warning | unknown
    last: float | None = None
    usual: float | None = None
    rhr_up: float = 0.0
    says: str = ""


def check(wellness, day: str) -> Breathing:
    w = sorted((x for x in wellness if x.date <= day), key=lambda x: x.date)
    if not w or w[-1].date != day or w[-1].breathing is None:
        return Breathing()
    hist = [x.breathing for x in w[-31:-1] if x.breathing]
    if len(hist) < 10:
        return Breathing(last=w[-1].breathing)
    usual = statistics.median(hist)
    last = w[-1].breathing
    rhist = [x.rhr for x in w[-31:-1] if x.rhr]
    rhr_up = (w[-1].rhr - statistics.median(rhist)) if (w[-1].rhr and len(rhist) >= 10) else 0.0
    up = last - usual
    if up >= 1.0 and rhr_up >= 3:
        return Breathing("warning", last, usual, rhr_up,
                         f"Breathing is up overnight ({last:.1f} a minute against your usual {usual:.1f}) "
                         f"and so is resting HR (+{rhr_up:.0f}). Together that's often the first sign you're "
                         f"fighting something off, before you feel it.")
    if up >= 1.0:
        return Breathing("up", last, usual, rhr_up,
                         f"Breathing was up overnight ({last:.1f} a minute against your usual {usual:.1f}). "
                         f"On its own it can be a late meal, alcohol or altitude; watch it tomorrow.")
    return Breathing("normal", last, usual, rhr_up, "")
