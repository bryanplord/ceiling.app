from datetime import date, timedelta

import pytest

from rx import demo
from rx import profile as prof
from rx.analysis import load as la
from rx.analysis.recovery import AMBER, GREEN, RED, Recovery, Signal, assess
from rx.prescribe import engine as eng
from rx.prescribe.score import score
from rx.store import Store, Wellness

TODAY = date(2026, 10, 12)


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "t.db")
    demo.seed(s, days=120, end=TODAY)
    return s


def rec(*statuses):
    names = ["HRV", "Resting HR", "Sleep"]
    return Recovery("2026-10-12", *[Signal(n, st, None, None, st) for n, st in zip(names, statuses)])


def day(load, atl, ctl):
    return la.LoadDay("2026-10-11", load, atl, ctl)


def test_profile_recovers_true_physiology(store):
    p = prof.build(store, TODAY.isoformat())
    assert abs(p.max_hr.bpm - demo.TRUE_MAX) <= 6
    assert p.max_hr.confirmed
    assert abs(p.threshold.bpm - demo.TRUE_LTHR) <= 8
    assert p.zones is not None


def test_two_reds_means_rest():
    v, _ = eng.decide(eng.Inputs(rec(RED, RED, GREEN), day(40, 40, 40), [40, 40]))
    assert v == eng.REST


def test_slept_well_but_load_too_high_means_light():
    v, why = eng.decide(eng.Inputs(rec(GREEN, GREEN, GREEN), day(60, 80, 50), [60, 60]))
    assert v == eng.LIGHT
    assert any("above your 6-week norm" in w for w in why)


def test_big_day_but_recovered_means_steady_not_hard():
    v, _ = eng.decide(eng.Inputs(rec(GREEN, GREEN, GREEN), day(120, 50, 48), [30, 120]))
    assert v == eng.STEADY


def test_fresh_and_recovered_means_hard():
    v, _ = eng.decide(eng.Inputs(rec(GREEN, GREEN, GREEN), day(30, 45, 50), [30, 30]))
    assert v == eng.HARD


def test_no_data_is_conservative():
    from rx.analysis.recovery import UNKNOWN
    v, why = eng.decide(eng.Inputs(rec(UNKNOWN, UNKNOWN, UNKNOWN), day(30, 45, 50), [30, 30]))
    assert v == eng.EASY


def test_full_prescription_has_five_parts_and_numbers(store):
    p = prof.build(store, TODAY.isoformat())
    rx = eng.build_rx(p)
    assert rx.headline and rx.why and rx.do and rx.dont and rx.tonight
    assert rx.hr_ceiling and rx.max_minutes
    assert str(rx.hr_ceiling) in rx.do or str(rx.hr_ceiling) in rx.dont
    assert "PM" in rx.tonight or "Normal bedtime" in rx.tonight
    assert rx.scenarios


def test_bad_night_turns_into_rest(store):
    ds = TODAY.isoformat()
    store.upsert_wellness("garmin", Wellness(ds, hrv=35, rhr=60, sleep_score=45))
    p = prof.build(store, ds)
    rx = eng.build_rx(p)
    assert rx.verdict in (eng.REST, eng.LIGHT)
    assert "Walk" in rx.do and "No running" in rx.dont or "No training" in rx.dont
    assert rx.tonight.startswith("In bed by")


def test_score_penalizes_ceiling_and_duration():
    rx = eng.Rx("2026-10-12", eng.STEADY, "Go.", [], "", "", "", "run", 130, 145, 55)
    t, h = demo.hr_session(55, 138, [])
    good = score(rx, t, h)
    assert good.score >= 90
    t2, h2 = demo.hr_session(70, 138, [(30, 15, 158)])
    bad = score(rx, t2, h2)
    assert bad.score < 60 and bad.minutes_over >= 10 and bad.over_duration >= 10


def test_breathing_and_rhr_up_together_caps_the_day(store):
    p = prof.build(store, TODAY.isoformat())
    day = TODAY.isoformat()
    for w in p.wellness:
        w.breathing = 12.5
    last = next(w for w in p.wellness if w.date == day)
    last.breathing, last.rhr = 14.3, (last.rhr or 50) + 6
    rx = eng.build_rx(p)
    assert eng.ORDER.index(rx.verdict) <= eng.ORDER.index(eng.EASY)
    assert "breathing" in rx.why_story.lower()
    assert any(e.name == "Breathing" for e in rx.reading.evidence)


def test_breathing_normal_changes_nothing(store):
    p = prof.build(store, TODAY.isoformat())
    base = eng.build_rx(p).verdict
    for w in p.wellness:
        w.breathing = 12.5
    assert eng.build_rx(p).verdict == base
