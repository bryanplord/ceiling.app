from datetime import date

from fastapi.testclient import TestClient

from rx import demo
from rx.store import Store


def test_pages_render(tmp_path, monkeypatch):
    monkeypatch.setenv("RX_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RX_OPEN", "1")
    monkeypatch.delenv("INTERVALS_API_KEY", raising=False)
    demo.seed(Store(tmp_path / "rx.db"), days=90, end=date.today())
    from rx.web.app import app
    c = TestClient(app)
    for path in ("/", "/plan", "/yesterday", "/recovery", "/progress", "/zones", "/trends", "/data", "/you", "/profile"):
        r = c.get(path)
        assert r.status_code == 200, path
        assert "<h1" in r.text
    j = c.get("/api/today").json()
    assert j["verdict"] and j["do"] and j["tonight"]


def test_password_login(tmp_path, monkeypatch):
    monkeypatch.setenv("RX_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("RX_OPEN", raising=False)
    monkeypatch.delenv("INTERVALS_API_KEY", raising=False)
    monkeypatch.setenv("RX_SECRET", "x" * 40)
    from rx.web import auth
    monkeypatch.setenv("RX_USERS", f"bryan@example.com={auth.hash_password('correct horse')},"
                                    f"jack@example.com={auth.hash_password('battery staple')}")
    demo.seed(Store(tmp_path / "rx.db"), days=30, end=date.today())
    from rx.web.app import app
    c = TestClient(app)
    form = {"content-type": "application/x-www-form-urlencoded"}

    # home page is the sign-in page; everything else is locked
    r = c.get("/")
    assert r.status_code == 200 and 'name="password"' in r.text and "Ideal" not in r.text
    assert c.get("/profile", follow_redirects=False).headers["location"] == "/"
    assert c.get("/api/today").status_code == 401
    assert c.get("/healthz").status_code == 200
    assert c.get("/static/favicon.svg").status_code == 200

    # wrong password, unknown email
    r = c.post("/login", content="email=bryan%40example.com&password=nope", headers=form)
    assert "don't match" in r.text
    r = c.post("/login", content="email=who%40example.com&password=correct+horse", headers=form)
    assert "don't match" in r.text

    # right password (email case-insensitive)
    r = c.post("/login", content="email=Jack%40Example.com&password=battery+staple",
               headers=form, follow_redirects=False)
    assert r.status_code == 303 and auth.COOKIE in r.cookies
    assert "Ideal" in c.get("/").text or "<h1" in c.get("/").text
    assert c.get("/profile").status_code == 200

    # removing an account locks it out even with a valid cookie
    monkeypatch.setenv("RX_USERS", f"bryan@example.com={auth.hash_password('x')}")
    assert c.get("/profile", follow_redirects=False).status_code == 303

    # sign out
    c.get("/logout")
    assert 'name="password"' in c.get("/").text


def test_sync_button(tmp_path, monkeypatch):
    monkeypatch.setenv("RX_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RX_OPEN", "1")
    monkeypatch.delenv("INTERVALS_API_KEY", raising=False)
    from rx.web.app import app
    c = TestClient(app)
    r = c.post("/sync", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/")


def test_calendar_feed_needs_a_signed_key(tmp_path, monkeypatch):
    monkeypatch.setenv("RX_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("RX_OPEN", raising=False)
    monkeypatch.setenv("RX_SECRET", "x" * 40)
    from rx.web import auth
    monkeypatch.setenv("RX_USERS", "bryan@example.com=" + auth.hash_password("pw"))
    demo.seed(Store(tmp_path / "rx.db"), days=90, end=date.today())
    from rx.web.app import app
    c = TestClient(app)
    assert c.get("/plan.ics").status_code == 404
    assert c.get("/plan.ics?k=nonsense").status_code == 404
    r = c.get("/plan.ics?k=" + auth.sign("cal", "bryan@example.com", 3600))
    assert r.status_code == 200 and "BEGIN:VEVENT" in r.text
    assert c.get("/plan.ics?k=" + auth.sign("session", "bryan@example.com", 3600)).status_code == 404
