from datetime import date
import os
import tempfile

from rx import rules as R
from rx.store import Store


def _s():
    return Store(os.path.join(tempfile.mkdtemp(), "r.db"))


def test_parse_standing_rules():
    d = date(2026, 10, 14)
    r = R.parse("Healing from a crash, no lifting or running for 4 weeks", d)
    assert r["bans"] == ["lift", "run"] and r["cap"] == "easy" and r["until"] == "2026-11-11"
    t = R.parse("I'm traveling today, and for the next 6 days", d)
    assert t["travel"] and t["until"] == "2026-10-20"
    assert R.parse("feeling tired", d) is None
    assert R.ended_by("all healed, back to normal")


def test_rules_steer_the_day_and_ask_to_lift_again():
    s = _s()
    R.save_setup(s, {"sports": ["ride", "lift"]})
    R.add(s, R.parse("no lifting for 1 week", date(2026, 10, 1)))
    js = R.merge(None, s, "2026-10-03")
    assert js["nolift"] and "run" in js["ban"] and "trail run" in js["ban"]      # you don't run: never suggested
    assert R.lapsed_lift(s, "2026-10-03") is None
    r = R.lapsed_lift(s, "2026-10-10")                                           # the week is up: ask
    assert r is not None
    R.answer(s, r["id"], False, "2026-10-10")                                    # not yet: another week
    assert R.lapsed_lift(s, "2026-10-12") is None and R.merge(None, s, "2026-10-12")["nolift"]
