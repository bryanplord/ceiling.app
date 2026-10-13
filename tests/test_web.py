from datetime import date

from fastapi.testclient import TestClient

from rx import demo
from rx.store import Store


def test_pages_render(tmp_path, monkeypatch):
    monkeypatch.setenv("RX_DATA_DIR", str(tmp_path))
    demo.seed(Store(tmp_path / "rx.db"), days=90, end=date.today())
    from rx.web.app import app
    c = TestClient(app)
    for path in ("/", "/yesterday", "/recovery", "/profile"):
        r = c.get(path)
        assert r.status_code == 200, path
        assert "<h1>" in r.text
    j = c.get("/api/today").json()
    assert j["verdict"] and j["do"] and j["tonight"]
