from datetime import date, timedelta

import pytest

from rx import demo
from rx import goals as gl
from rx import profile as prof
from rx.prescribe import engine as eng
from rx.prescribe import plan as pm
from rx.store import Store

TODAY = date(2026, 10, 13)
KINDS = {"rest", "light", "easy", "steady", "hard", "long", "race"}


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    s = Store(tmp_path_factory.mktemp("o") / "t.db")
    demo.seed(s, days=120, end=TODAY)
    p = prof.build(s, TODAY.isoformat())
    return p, eng.build_rx(p)


def _weeks(rows):
    out = {}
    for r in rows:
        d = date.fromisoformat(r["date"])
        out.setdefault(d - timedelta(days=d.weekday()), []).append(r)
    return out


def test_no_plan_is_six_weeks_of_easy_and_steady(built):
    p, rx = built
    p.program, p.programs = None, []
    rows = pm.outlook(p, rx)
    assert 40 <= len(rows) <= 44
    assert rows[0]["date"] == rx.date and rows[0]["today"]
    assert [r["date"] for r in rows] == sorted({r["date"] for r in rows})
    firm = [r for r in rows if r["firm"]]
    assert firm and all(r["date"] <= firm[-1]["date"] for r in firm)
    pencil = [r for r in rows if not r["firm"]]
    assert {r["kind"] for r in pencil} <= {"rest", "easy", "steady"}
    for r in pencil:
        if r["kind"] == "rest":
            assert r["minutes"] is None
        else:
            assert 20 <= r["minutes"] <= 120


def test_rest_only_on_weekdays_you_rarely_train(built):
    p, rx = built
    p.program, p.programs = None, []
    share = pm._weekday_share(p, rx.date)
    for r in pm.outlook(p, rx):
        if not r["firm"] and r["kind"] == "rest":
            assert share[date.fromisoformat(r["date"]).weekday()] < 0.1


def test_marathon_programme(built):
    p, rx = built
    p.program = prog = gl.build(p, gl.parse_text("Run a marathon on February 1 2027", TODAY), TODAY)
    p.programs = [prog]
    long_day = getattr(prog, "long_day", 6)
    rows = pm.outlook(p, rx)
    assert rows[-1]["date"] == "2027-02-01" and rows[-1]["kind"] == "race"
    assert {r["kind"] for r in rows} <= KINDS
    weeks = {w.start: w for w in prog.weeks}
    for mon, rs in _weeks(rows).items():
        w = weeks.get(mon.isoformat())
        if not w:
            continue
        pencil = [r for r in rs if not r["firm"]]
        if not pencil:
            continue
        assert sum(r["kind"] == "hard" for r in pencil) <= w.hard_days
        assert all(r["week_phase"] == w.phase for r in rs)
        for r in pencil:
            if r["kind"] == "long":
                assert date.fromisoformat(r["date"]).weekday() == long_day
                assert abs(r["minutes"] - w.long_minutes) <= 5
        sun = next((r for r in pencil if date.fromisoformat(r["date"]).weekday() == long_day), None)
        if sun and w.long_minutes:
            assert sun["kind"] == "long"
    # never two hard days back to back
    ks = [r["kind"] for r in rows]
    assert not any(a == b == "hard" for a, b in zip(ks, ks[1:]))


def test_month_grids(built):
    p, rx = built
    p.program = gl.build(p, gl.parse_text("Run a marathon on February 1 2027", TODAY), TODAY)
    p.programs = [p.program]
    ms = pm.months(pm.outlook(p, rx))
    assert [m["ym"] for m in ms] == ["2026-10", "2026-11", "2026-12", "2027-01", "2027-02"]
    assert ms[-1]["race"]
    for m in ms:
        assert all(len(w["cells"]) == 7 for w in m["weeks"])
    oct_ = ms[0]
    assert any(c and c.get("past") for w in oct_["weeks"] for c in w["cells"])


def _check_weeks(rows, prog):
    """Every fully sketched week sums to its target hours (within 15%) and no session is below its floor."""
    weeks = {w.start: w for w in prog.weeks}
    checked = 0
    for mon, rs in _weeks(rows).items():
        w = weeks.get(mon.isoformat())
        if not w or len(rs) < 7 or any(r["firm"] for r in rs) or any(r["kind"] == "race" for r in rs):
            continue
        tot = sum(r["minutes"] or 0 for r in rs)
        assert abs(tot - w.hours * 60) <= 0.15 * w.hours * 60, (mon, tot, w.hours)
        for r in rs:
            if r["kind"] in pm.FLOOR:
                assert r["minutes"] >= pm.FLOOR[r["kind"]], (r["date"], r["kind"], r["minutes"])
            if r["kind"] == "long":
                assert abs(r["minutes"] - w.long_minutes) <= 5
        checked += 1
    assert checked >= 5


def test_week_hours_add_up(built):
    p, rx = built
    p.program = prog = gl.build(p, gl.parse_text("Run a marathon on February 1 2027", TODAY), TODAY)
    p.programs = [prog]
    _check_weeks(pm.outlook(p, rx), prog)


def test_low_hours_drop_sessions_not_minutes(built, monkeypatch):
    """3.4 hours in a week you train six days: fewer, real sessions, never four 20-minute ones."""
    p, rx = built
    p.program = prog = gl.build(p, gl.parse_text("Run a marathon on February 1 2027", TODAY), TODAY)
    p.programs = [prog]
    for w in prog.weeks:
        w.hours = max(3.4, round(w.hours * 0.55, 1))
        if w.long_minutes:
            w.long_minutes = int(round(w.long_minutes * 0.55 / 5) * 5)
    monkeypatch.setattr(pm, "_weekday_share", lambda p, d: [0.6] * 7)   # trains every day
    rows = pm.outlook(p, rx)
    _check_weeks(rows, prog)
    for r in rows:
        if not r["firm"] and r["kind"] in ("easy", "steady", "hard"):
            nxt = next((x for x in rows if x["date"] == (date.fromisoformat(r["date"]) + timedelta(days=1)).isoformat()), None)
            if not (nxt and nxt["kind"] == "race"):
                assert r["minutes"] >= pm.FLOOR[r["kind"]]


def test_one_confidence_scale(built):
    p, rx = built
    p.program = gl.build(p, gl.parse_text("Run a marathon on February 1 2027", TODAY), TODAY)
    p.programs = [p.program]
    plan = pm.build(p, rx)
    assert [d.confidence for d in plan.days][:2] == ["Today", "Likely"]
    assert all(d.confidence == "Probable" for d in plan.days[2:])
    rows = pm.outlook(p, rx, plan=plan)
    assert {r["conf"] for r in rows} <= set(pm.CONF)
    order = [pm.CONF.index(r["conf"]) for r in rows]
    assert order == sorted(order)                      # confidence only ever drops with distance
    for r in rows:
        if r["conf"] == "Sketch" and r["kind"] in ("easy", "steady", "hard", "long"):
            assert r["aim"] and r["week_phase"]
        if r["conf"] != "Sketch":
            assert r["title"]
