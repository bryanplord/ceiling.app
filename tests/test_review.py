import os
import tempfile
from datetime import date, datetime


def test_review_energy_and_after(monkeypatch):
    d = tempfile.mkdtemp()
    monkeypatch.setenv("RX_DATA_DIR", d)
    from rx import demo
    from rx.store import Store
    s = Store(os.path.join(d, "rx.db"))
    demo.seed(s, days=120, end=date.today())
    from rx import profile as prof
    from rx.prescribe.engine import build_rx
    from rx.review import review, LiteRx
    from rx.analysis import energy
    day = date.today().isoformat()
    p = prof.build(s, day)
    rx = build_rx(p, day)
    a = next(a for a in reversed(p.activities) if a.sport != "walk" and s.stream(a.id))
    rv = review(p, rx, a, s.stream(a.id))
    assert rv and 0 <= rv.score <= 100 and rv.word and rv.headline
    assert sum(rv.parts.values()) >= rv.score - 1
    lite = review(p, LiteRx(a.date, rx.verdict, rx.hr_low, rx.hr_ceiling), a, s.stream(a.id))
    assert lite is not None
    e = energy.build(p, day, datetime.now().replace(hour=15, minute=0))
    assert e and 0 <= e.now <= e.wake <= 100
