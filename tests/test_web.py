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
    for path in ("/", "/yesterday", "/recovery", "/progress", "/profile"):
        r = c.get(path)
        assert r.status_code == 200, path
        assert "<h1>" in r.text
    j = c.get("/api/today").json()
    assert j["verdict"] and j["do"] and j["tonight"]


def test_magic_link(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RX_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("RX_OPEN", raising=False)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("INTERVALS_API_KEY", raising=False)
    monkeypatch.setenv("RX_SECRET", "x" * 40)
    monkeypatch.setenv("RX_ALLOWED_EMAILS", "bryan@example.com, jack@example.com")
    demo.seed(Store(tmp_path / "rx.db"), days=30, end=date.today())
    from rx.web.app import app
    from rx.web import auth
    c = TestClient(app)

    # locked by default
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert c.get("/api/today").status_code == 401
    assert c.get("/healthz").status_code == 200

    # stranger gets the same page, no link
    r = c.post("/login", content="email=stranger@example.com",
               headers={"content-type": "application/x-www-form-urlencoded"})
    assert "Check your email" in r.text
    assert "stranger" not in capsys.readouterr().out

    # allowed address gets a link (printed when no email provider is set)
    c.post("/login", content="email=Jack@Example.com",
           headers={"content-type": "application/x-www-form-urlencoded"})
    link = capsys.readouterr().out.strip().split()[-1]
    token = link.split("t=")[1]

    # forged or session-typed tokens don't work as links
    assert auth.verify("link", token[:-2] + "aa") is None
    assert auth.verify("session", token) is None

    r = c.get(f"/auth?t={token}", follow_redirects=False)
    assert r.status_code == 303 and auth.COOKIE in r.cookies
    assert c.get("/").status_code == 200

    # removing someone from the list locks them out even with a valid cookie
    monkeypatch.setenv("RX_ALLOWED_EMAILS", "bryan@example.com")
    assert c.get("/", follow_redirects=False).status_code == 303
