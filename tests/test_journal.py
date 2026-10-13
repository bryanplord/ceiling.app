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
