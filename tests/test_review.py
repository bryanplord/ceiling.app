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
    assert sum(v for v, m in rv.parts.values()) >= rv.score - 1 and sum(m for v, m in rv.parts.values()) == 100
    lite = review(p, LiteRx(a.date, rx.verdict, rx.hr_low, rx.hr_ceiling), a, s.stream(a.id))
    assert lite is not None
    e = energy.build(p, day, datetime.now().replace(hour=15, minute=0))
    assert e and 0 <= e.now <= e.wake <= 100


def test_energy_bounds_and_drain():
    """Synthetic day shaped like Oct 13: 7h40m, score 77, HRV 72, RHR 46, a 31-min easy run."""
    from datetime import timedelta
    from types import SimpleNamespace as NS
    from rx.analysis import energy
    day = date(2026, 10, 13)
    ws = []
    for i in range(60, 0, -1):
        d = (day - timedelta(days=i)).isoformat()
        ws.append(NS(date=d, hrv=72 + (i % 5) - 2, sleep_secs=8 * 3600, sleep_score=80, rhr=47 + (i % 3) - 1))
    for i, h in zip(range(6, 0, -1), [60, 62, 64, 66, 64, 62]):
        ws[-i].hrv = h
    ws.append(NS(date=day.isoformat(), hrv=72, sleep_secs=7 * 3600 + 40 * 60, sleep_score=77, rhr=46))
    run = NS(id="r1", date=day.isoformat(), sport="run", name="Easy run", start_local="2026-10-13T12:00:00", moving_s=31 * 60)
    p = NS(wellness=ws, load_days=[NS(date=(day - timedelta(days=1)).isoformat(), tsb=2.0)],
           activities=[run], session_load={"r1": 29.0})
    vals = [energy.build(p, day.isoformat(), datetime(2026, 10, 13, h, 0)) for h in range(7, 23)]
    assert all(e and 0 <= e.now <= e.wake <= 100 for e in vals)
    assert all(a.now >= b.now for a, b in zip(vals, vals[1:]))          # drain only goes down
    e = energy.build(p, day.isoformat(), datetime(2026, 10, 13, 15, 30))
    assert 40 <= e.now <= 65 and 70 <= e.wake <= 95                    # Garmin read 51 at that time
    hrs = [h for h, _ in e.curve]
    assert hrs == sorted(hrs) and hrs[0] < 0                             # starts the evening before
    awake = [v for h, v in e.curve if h >= 7]
    asleep = [v for h, v in e.curve if h < 7]
    assert all(a <= b + 1e-9 for a, b in zip(asleep, asleep[1:]))       # charges overnight
    assert all(a >= b - 1e-9 for a, b in zip(awake, awake[1:]))         # drains through the day
    assert asleep[0] < e.wake and abs(awake[0] - e.wake) < 1e-9
    # harder minutes cost more than their load alone
    assert energy.session_cost(60, 30 * 60) > 2 * energy.session_cost(30, 30 * 60)


def _fake(p, sport, minutes, hr_fn, kind="Run"):
    from rx.store import Activity
    t = list(range(minutes * 60))
    hr = [hr_fn(i) for i in t]
    a = Activity(id="x", source="t", date=p.as_of, sport=sport, moving_s=minutes * 60, raw_type=kind,
                 avg_hr=sum(hr) / len(hr))
    return a, (t, hr, None)


def test_scores_match_the_kind_of_day(tmp_path, monkeypatch):
    """Easy days are about staying easy; hard days about doing the hard work."""
    import os
    from datetime import date
    monkeypatch.setenv("RX_DATA_DIR", str(tmp_path))
    from rx import demo, profile as prof
    from rx.store import Store
    from rx.review import review, LiteRx
    s = Store(os.path.join(str(tmp_path), "rx.db"))
    demo.seed(s, days=90, end=date.today())
    p = prof.build(s, date.today().isoformat())
    d = p.as_of
    easy = LiteRx(d, "easy", 123, 133, 40, "run")
    # 31 min mostly well under the range, one ~1-minute push to 142: an easy day done right
    a, st = _fake(p, "run", 31, lambda i: 142 if 900 <= i < 960 else 117)
    good = review(p, easy, a, st)
    assert good.score >= 95, good.score
    # same length but 26 minutes over the ceiling: an easy day done wrong
    a, st = _fake(p, "run", 31, lambda i: 148 if i >= 300 else 120)
    bad = review(p, easy, a, st)
    assert bad.score < 50, bad.score
    # a short easy day costs nothing
    a, st = _fake(p, "run", 20, lambda i: 118)
    assert review(p, easy, a, st).score >= 95
    # hard day, never over the ceiling but no threshold work: not a hard day
    hard = LiteRx(d, "hard", 152, 163, 75, "bike")
    a, st = _fake(p, "bike", 75, lambda i: 130, kind="Ride")
    assert review(p, hard, a, st).score < 50
    # hard day with 4 x 8 at threshold: done
    a, st = _fake(p, "bike", 75, lambda i: 156 if (i // 60) in set(range(15, 23)) | set(range(27, 35)) | set(range(39, 47)) | set(range(51, 59)) else 125, kind="Ride")
    assert review(p, hard, a, st).score >= 90
