"""Changing a plan in your own words: a table of phrases and what they should do."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from rx import demo, goals as gl
from rx.store import Store

D = date(2026, 10, 13)          # a Tuesday


def mar(**kw):
    base = dict(kind="event", event="marathon", sport="run", target_date="2027-03-07")
    base.update(kw)
    return gl.Goal(**base)


def wt():
    return gl.Goal("weight", target_date="2027-01-10", start_weight=181, target_weight=172)


CASES = [
    # (goal, phrase, expected changes, check)
    (mar(), "4:20", ["Target: 4:20 (9:55 a mile)"], lambda g: g.target == "4:20"),
    (mar(), "sub 4", ["Target: sub 4:00 (9:09 a mile)"], lambda g: gl.target_seconds(g) == 4 * 3600),
    (mar(), "under 4 hours", ["Target: sub 4:00 (9:09 a mile)"], None),
    (mar(), "4 hours 20", ["Target: 4:20 (9:55 a mile)"], None),
    (mar(), "Move the race to Feb 14", ["Race moved to Sunday, Feb 14"], lambda g: g.target_date == "2027-02-14"),
    (mar(), "race is March 1st", ["Race moved to Monday, Mar 1"], lambda g: g.target_date == "2027-03-01"),
    (mar(), "push it back two weeks", ["Race moved to Sunday, Mar 21"], None),
    (mar(), "a week earlier", ["Race moved to Sunday, Feb 28"], None),
    (mar(), "max 6 hours a week", ["At most 6 hours a week"], lambda g: g.max_hours == 6),
    (mar(), "no more than 5 hrs", ["At most 5 hours a week"], lambda g: g.max_hours == 5),
    (mar(), "can't train Wednesdays", ["No training on Wednesdays"], lambda g: g.no_days == [2]),
    (mar(), "No Mondays or Fridays", ["No training on Mondays or Fridays"], lambda g: g.no_days == [0, 4]),
    (mar(no_days=[2]), "I can train Wednesdays now", ["Wednesdays are back on"], lambda g: g.no_days == []),
    (mar(), "long runs on Saturday", ["Long runs on Saturdays"], lambda g: g.long_day == 5),
    (mar(), "make it a half marathon", ["Event: half marathon"], lambda g: g.event == "half marathon"),
    (mar(target="4:20"), "switch to a 50k", ["Event: 50k ultra", "Target time cleared (it was for the marathon)"],
     lambda g: g.event == "ultra" and g.target == ""),
    (mar(), "gran fondo instead", ["Event: gran fondo"], lambda g: g.sport == "bike"),
    (wt(), "lose 10 lb", ["Target weight: 171 lb, 10 lb to lose"], lambda g: g.target_weight == 171),
    (wt(), "target 170", ["Target weight: 170 lb, 11 lb to lose"], lambda g: g.target_weight == 170),
    (mar(), "Make it a 4:20 marathon. I can't train Wednesdays. Max 6 hours a week. Move the race to February 14.",
     ["Target: 4:20 (9:55 a mile)", "No training on Wednesdays", "At most 6 hours a week", "Race moved to Sunday, Feb 14"],
     None),
]


@pytest.mark.parametrize("goal,phrase,want,check", CASES, ids=[c[1][:40] for c in CASES])
def test_phrases(goal, phrase, want, check):
    g, ch = gl.apply_text(goal, phrase, D)
    assert list(ch) == want
    assert not ch.stop and not ch.missed
    if check:
        assert check(g)


def test_does_not_touch_the_original():
    g0 = mar(no_days=[1])
    gl.apply_text(g0, "no Wednesdays, 4:20", D)
    assert g0.no_days == [1] and g0.target == ""


def test_unknown_changes_nothing_and_says_so():
    g0 = mar()
    g, ch = gl.apply_text(g0, "make it purple", D)
    assert list(ch) == [] and g == g0 and not ch.understood
    assert "didn't understand" in ch.notes[0] and "4:20 marathon" in ch.notes[0]


def test_partly_understood_names_the_rest():
    g, ch = gl.apply_text(mar(), "4:20, and I love pizza", D)
    assert list(ch) == ["Target: 4:20 (9:55 a mile)"]
    assert "I love pizza" in ch.notes[0]


@pytest.mark.parametrize("phrase", ["I want to quit", "stop this plan", "I give up"])
def test_quit_asks_never_stops(phrase):
    g0 = mar(status="active", committed="2026-10-01")
    g, ch = gl.apply_text(g0, phrase, D)
    assert ch.stop and list(ch) == [] and g.status == "active"


def test_stop_training_on_a_day_is_a_day_off_not_quitting():
    g, ch = gl.apply_text(mar(), "stop training on Wednesdays", D)
    assert not ch.stop and g.no_days == [2]


def test_refuses_past_dates_and_silly_times():
    g, ch = gl.apply_text(mar(), "race is May 1, 2026", D)
    assert list(ch) == [] and "has passed" in ch.notes[0]
    g, ch = gl.apply_text(gl.Goal("event", event="10k", target_date="2027-03-07"), "4:20", D)
    assert list(ch) == [] and "doesn't fit" in ch.notes[0]


def test_building_and_adjusting_share_a_parser():
    g, _ = gl.from_text("Run a 4:20 marathon on Feb 1, no Wednesdays", D)
    assert (g.kind, g.event, g.target_date, g.target, g.no_days) == ("event", "marathon", "2027-02-01", "4:20", [2])
    g, _ = gl.from_text("Ride a gran fondo June 6, max 8 hours a week", D)
    assert g.event == "gran fondo" and g.max_hours == 8 and g.target_date == "2027-06-06"


def test_long_day_moves_the_long_session():
    g, _ = gl.apply_text(mar(), "long runs on Saturday", D)
    assert gl.long_weekday(g) == 5
    g, _ = gl.apply_text(g, "no Saturdays", D)
    assert g.long_day is None and gl.long_weekday(g) == 6


def test_routes(tmp_path, monkeypatch):
    monkeypatch.setenv("RX_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RX_OPEN", "1")
    s = Store(tmp_path / "rx.db")
    demo.seed(s, days=40, end=date.today())
    from rx.web.app import app
    c = TestClient(app)
    r = c.post("/plan", data={"text": "Run a marathon in March, no Mondays"}, follow_redirects=False)
    pid = r.headers["location"].rsplit("/", 1)[1]
    g = gl.get(s, pid)
    assert g.status == "draft" and g.no_days == [0]
    page = c.get(f"/plan/{pid}").text
    assert "Change anything in your own words" in page and "Or adjust the details" in page
    r = c.post(f"/plan/{pid}/say", data={"say": "Make it 4:20. No Wednesdays."})
    assert "Preview updated" in r.text and "Target: 4:20" in r.text and "No training on Wednesdays" in r.text
    assert gl.get(s, pid).no_days == [0, 2]
    c.post(f"/plan/{pid}/commit", data={})
    committed = gl.get(s, pid).committed
    r = c.post(f"/plan/{pid}/say", data={"say": "max 5 hours a week"})
    assert "Updated. Today and the calendar follow the new plan." in r.text
    g = gl.get(s, pid)
    assert g.status == "active" and g.committed == committed and g.max_hours == 5
    r = c.post(f"/plan/{pid}/say", data={"say": "make it purple"})
    assert "understand that, so nothing changed" in r.text and "make it purple" in r.text
    r = c.post(f"/plan/{pid}/say", data={"say": "I want to quit"})
    assert "Stop this plan from today?" in r.text and gl.get(s, pid).status == "active"
