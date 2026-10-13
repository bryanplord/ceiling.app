import json
from datetime import date, timedelta

from fastapi.testclient import TestClient

from rx import demo, goals as gl
from rx.store import Store

D = date(2026, 10, 13)


def _store(tmp_path):
    return Store(tmp_path / "rx.db")


def test_migrates_old_single_goal(tmp_path):
    s = _store(tmp_path)
    s.set_meta("goal", json.dumps({"kind": "event", "event": "marathon", "sport": "run",
                                   "target_date": "2027-05-02", "created": "2026-09-01"}))
    plans = gl.all_plans(s)
    assert len(plans) == 1 and plans[0].status == "active" and plans[0].committed == "2026-09-01"
    assert plans[0].id and not s.get_meta("goal")
    assert [g.id for g in gl.active(s, D)] == [plans[0].id]
    assert len(gl.all_plans(s)) == 1                     # migrates once


def test_commit_stop_delete(tmp_path):
    s = _store(tmp_path)
    g = gl.save_plan(s, gl.Goal("best_shape", target_date="2027-02-01"))
    assert g.status == "draft" and gl.active(s, D) == []     # drafts drive nothing
    gl.commit(s, g.id, D)
    assert [x.id for x in gl.active(s, D)] == [g.id]
    assert gl.active(s, D - timedelta(days=1)) == []         # not before you committed
    stop_day = D + timedelta(days=10)
    gl.stop(s, g.id, stop_day)
    g2 = gl.get(s, g.id)
    assert g2.status == "stopped" and g2.stopped_on == stop_day.isoformat()
    assert [x.id for x in gl.active(s, stop_day - timedelta(days=1))] == [g.id]   # the days before keep it
    assert gl.active(s, stop_day) == [] and gl.active(s, stop_day + timedelta(days=30)) == []
    gl.delete(s, g.id)
    assert gl.all_plans(s) == []


def test_two_active_plans_merge(tmp_path):
    s = _store(tmp_path)
    demo.seed(s, days=60, end=D)
    from rx import profile as prof
    p = prof.build(s, D.isoformat())
    a = gl.save_plan(s, gl.Goal("longevity"))
    b = gl.save_plan(s, gl.Goal("event", event="marathon", sport="run", target_date="2027-03-07", no_days=[1]))
    gl.commit(s, a.id, D)
    gl.commit(s, b.id, D)
    act = gl.active(s, D)
    assert {x.id for x in act} == {a.id, b.id}
    progs = [gl.build(p, g, D) for g in act]
    assert gl.primary(progs, D).goal.id == b.id               # the dated event leads
    tue = D + timedelta(days=(1 - D.weekday()) % 7)
    dd = gl.daily_all(progs, tue)
    assert dd["rest"] is True and dd["sport"] == "run" and dd["plan"] == b.id
    sun = D + timedelta(days=(6 - D.weekday()) % 7)
    dd = gl.daily_all(progs, sun)
    assert dd["hard_days"] == min(gl.daily(x, sun)["hard_days"] for x in progs)
    assert set(dd["plans"]) == {a.id, b.id}
    assert gl.daily_all([], D) == {}


def test_finish_time_target(tmp_path):
    g = gl.Goal("event", event="marathon", sport="run", target="4:20", target_date="2027-05-02")
    assert gl.target_seconds(g) == 4 * 3600 + 20 * 60
    assert gl.pace_per_mile(g) == "9:55"
    assert gl.target_seconds(gl.Goal("event", event="half marathon", target="sub 1:55")) == 6900
    assert gl.target_seconds(gl.Goal("event", event="10k", target="48:30")) == 2910
    assert gl.target_seconds(gl.Goal("event", event="marathon", target="finish strong")) is None
    s = _store(tmp_path)
    demo.seed(s, days=60, end=D)
    from rx import profile as prof
    p = prof.build(s, D.isoformat())
    prog = gl.build(p, g, D)
    assert prog.title.startswith("Marathon in 4:20") and prog.pace == "9:55"
    assert any("9:55" in w.note for w in prog.weeks)
    capped = gl.build(p, gl.Goal("event", event="marathon", target_date="2027-05-02", max_hours=4), D)
    assert max(w.hours for w in capped.weeks) <= 4


def test_plan_pages_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("RX_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RX_OPEN", "1")
    monkeypatch.delenv("INTERVALS_API_KEY", raising=False)
    demo.seed(Store(tmp_path / "rx.db"), days=60, end=date.today())
    from rx.web.app import app
    c = TestClient(app)
    form = {"content-type": "application/x-www-form-urlencoded"}
    assert "What are you training for?" in c.get("/plan").text
    r = c.post("/plan", content="canned=marathon&target=4%3A20", headers=form, follow_redirects=False)
    pid = r.headers["location"].rsplit("/", 1)[1]
    t = c.get(f"/plan/{pid}").text
    assert "Commit to this plan" in t and "Marathon in 4:20" in t
    r = c.post(f"/plan/{pid}/commit", content="edit=1&target=4%3A20&no=1&no=3&max_hours=6&sport=run",
               headers=form, follow_redirects=False)
    assert r.status_code == 303
    from rx.service import open_store, today_rx
    st = open_store()
    g = gl.get(st, pid)
    assert g.status == "active" and g.no_days == [1, 3] and g.max_hours == 6
    assert "You're committed" in c.get(f"/plan/{pid}").text
    p, _ = today_rx(st)
    assert p.program and p.program.goal.id == pid and len(p.programs) == 1
    t = c.get("/plan").text
    assert "Your plans" in t and "Committed" in t
    c.post(f"/plan/{pid}/stop", headers=form)
    assert gl.get(open_store(), pid).status == "stopped"
    p, _ = today_rx(open_store())
    assert p.program is None and p.programs == []
    c.post(f"/plan/{pid}/delete", headers=form)
    assert gl.all_plans(open_store()) == []
