import os
import tempfile
from datetime import date
from types import SimpleNamespace

from rx import strength as sg, goals as gl
from rx.store import Store


def _store():
    d = tempfile.mkdtemp()
    return Store(os.path.join(d, "rx.db"))


def test_double_progression():
    s = _store()
    sg.save_log(s, "2026-10-05", {"session": "lower", "sets": {"squat": [[50, 8, True], [50, 8, True], [50, 8, True]]}, "done": True})
    w, note = sg.prescribe_weight(s, "squat", 6, 8, 10, "2026-10-07", "lb")
    assert w == 60 and "Up 10 lb" in note
    sg.save_log(s, "2026-10-07", {"session": "lower", "sets": {"squat": [[60, 7, True], [60, 6, True], [60, 6, True]]}, "done": True})
    w, note = sg.prescribe_weight(s, "squat", 6, 8, 10, "2026-10-09", "lb")
    assert w == 60 and "one more rep" in note
    for d in ("2026-10-09", "2026-10-12"):
        sg.save_log(s, d, {"session": "lower", "sets": {"squat": [[60, 4, True], [60, 4, True]]}, "done": True})
    w, note = sg.prescribe_weight(s, "squat", 6, 8, 10, "2026-10-14", "lb")
    assert w == 50 and "Down" in note


def test_schedule_and_recovery():
    s = _store()
    g = gl.Goal("strength", lift_days=3, id="x")
    progs = [SimpleNamespace(goal=g)]
    mon = date(2026, 10, 12)
    plan = sg.plan_ahead(s, progs, mon, 7)
    assert list(plan.values()) == ["lower", "upper", "full"] and "2026-10-13" not in plan
    # miss Monday and the lift simply moves to the next day you can
    sg.save_log(s, "2026-10-13", {"session": "lower", "sets": {"squat": [[50, 8, True]]}})
    plan = sg.plan_ahead(s, progs, date(2026, 10, 14), 6)
    assert "2026-10-14" not in plan and plan["2026-10-15"] == "upper"
    # a hard day tomorrow keeps heavy legs off today
    sess = sg.session(s, "lower", mon.isoformat(), "steady", tomorrow="hard")
    assert sess.key == "upper" and "no heavy legs" in sess.why
    # a rough morning makes it lighter; a sick day makes it mobility
    assert sg.session(s, "lower", mon.isoformat(), "easy", recovery_low=True).rounds == 2
    assert sg.session(s, "lower", mon.isoformat(), "easy", sick=True).mode == "mobility"


def test_text_makes_a_strength_plan():
    g = gl.parse_text("I want to lift 4 times a week and get stronger", date(2026, 10, 13))
    assert g.kind == "strength" and g.lift_days == 4
