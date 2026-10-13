"""The engine must give identical answers on plain SQLite and on the Turso (libsql) client."""
from datetime import date

import pytest

from rx import demo
from rx import profile as prof
from rx.prescribe import engine as eng
from rx.store import Activity, Store, Wellness

TODAY = date(2026, 10, 12)


def build(store):
    demo.seed(store, days=60, end=TODAY)
    p = prof.build(store, TODAY.isoformat())
    return p, eng.build_rx(p)


def test_libsql_matches_sqlite(tmp_path):
    pytest.importorskip("libsql")
    p1, rx1 = build(Store(tmp_path / "a.db"))
    p2, rx2 = build(Store(tmp_path / "b.db", backend="libsql"))
    assert p1.max_hr.bpm == p2.max_hr.bpm
    assert p1.threshold.bpm == p2.threshold.bpm
    assert (rx1.verdict, rx1.do, rx1.if_skip) == (rx2.verdict, rx2.do, rx2.if_skip)


def test_libsql_single_row_reads(tmp_path):
    pytest.importorskip("libsql")
    s = Store(tmp_path / "c.db", backend="libsql")
    assert s.activity("nope") is None and s.get_meta("k") is None and s.stream("nope") is None
    s.upsert_activity(Activity("x", "demo", "2026-10-01", "run", 3000))
    s.upsert_stream("x", [0, 1], [120, 121], None, [3.0, 3.1])
    s.upsert_wellness("intervals", Wellness("2026-10-01", hrv=60, rhr=50))
    s.set_meta("k", "v")
    assert s.activity("x").sport == "run" and s.has_stream("x")
    assert s.speed("x") == [3.0, 3.1] and s.get_meta("k") == "v"
    assert s.wellness()[0].hrv == 60
