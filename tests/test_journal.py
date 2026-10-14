from datetime import date

from fastapi.testclient import TestClient

from rx import demo, journal as jr
from rx.store import Store


def test_view_shows_taps_and_what_text_said(tmp_path):
    st = Store(tmp_path / "rx.db")
    jr.save(st, "2026-10-13", "", {"feel": "tired"}, "7:05 AM")
    jr.save(st, "2026-10-13", "two beers and pizza", {}, "8:40 PM")
    tl = jr.timeline(jr.load(st, "2026-10-13"))
    assert [e["at"] for e in tl] == ["7:05 AM", "8:40 PM"]
    assert tl[0]["pills"] == [{"label": "Tired", "tone": "warn"}]
    assert {"label": "2 drinks", "tone": "drink"} in tl[1]["pills"] and tl[1]["text"] == "two beers and pizza"


def test_journal_post_returns_new_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("RX_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RX_OPEN", "1")
    monkeypatch.delenv("INTERVALS_API_KEY", raising=False)
    demo.seed(Store(tmp_path / "rx.db"), days=30, end=date.today())
    from rx.web.app import app
    c = TestClient(app)
    hdr = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
    j = c.post("/journal", content="feel=sick", headers=hdr).json()
    assert j["entry"]["pills"] == [{"label": "Sick", "tone": "bad"}]
    at = j["entry"]["at"]
    assert at.endswith(("AM", "PM")) and int(at.split(":")[0]) <= 12
    assert "easy" in j["note"]
    page = c.get("/").text
    assert 'class="pill on"' not in page and "checked" not in page.split('id="journal"')[1].split("</form>")[0]
    assert at in page


def test_pain_hungry_tired_signals(tmp_path):
    import json
    from datetime import date, timedelta
    from rx import journal as jr
    from rx.store import Store
    s = Store(str(tmp_path / "rx.db"))
    t = jr.parse("run was ok. feet hurt. I think I hate my shoes.")
    assert t["pain"] == ["feet"] and t.get("shoes")
    assert jr.parse("hungry").get("hungry")
    assert "no running" in jr.effect(t)[1]
    cap, note = jr.effect({"feel": "tired"}, hrv_low=True)
    assert cap == "steady"
    assert jr.effect({"feel": "tired"}, hrv_low=False)[0] is None
    today = date.today()
    y = (today - timedelta(days=1)).isoformat()
    jr.save(s, y, "feet hurt", {"feel": "tired"}, "5:35 PM")
    sig = jr.signals(s, today.isoformat(), hrv_low=True)
    assert sig["cap"] == "steady" and "run" in sig["ban"]
    v = jr.view({"text": "feet hurt", "taps": {}})
    assert any(p["label"] == "Sore feet" for p in v["pills"])


def test_journal_reaches_morning_prescription(tmp_path, monkeypatch):
    import os
    from datetime import date, timedelta
    monkeypatch.setenv("RX_DATA_DIR", str(tmp_path))
    from rx import demo, journal as jr, profile as prof
    from rx.store import Store
    from rx.prescribe.engine import build_rx
    s = Store(os.path.join(str(tmp_path), "rx.db"))
    demo.seed(s, days=90, end=date.today())
    day = date.today().isoformat()
    y = (date.today() - timedelta(days=1)).isoformat()
    jr.save(s, y, "my shins are sore", {}, "8:00 PM")
    p = prof.build(s, day)
    p.journal = jr.signals(s, day, False)
    rx = build_rx(p, day)
    assert rx.sport != "run"


def test_more_things_people_say(tmp_path):
    from datetime import date, timedelta
    from rx import journal as jr
    from rx.store import Store
    for text, tag in [("way over caffeinated", "caffeine"), ("bad headache", "headache"), ("slept badly", "bad_sleep"),
                      ("flew home tonight", "travel"), ("so hot and humid", "heat")]:
        assert jr.parse(text).get(tag), text
    assert not jr.parse("bad headache").get("sick")
    s = Store(str(tmp_path / "rx.db"))
    y = (date.today() - timedelta(days=1)).isoformat()
    jr.save(s, y, "headache all afternoon", {}, "4:00 PM")
    assert jr.signals(s, date.today().isoformat(), False, rhr_up=True)["cap"] == "easy"
    assert jr.signals(s, date.today().isoformat(), False, rhr_up=False)["cap"] is None
