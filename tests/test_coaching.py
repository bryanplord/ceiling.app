"""Measured thresholds, progress, and the price of not following the prescription."""
import random
from datetime import date

import pytest

from rx import demo
from rx import profile as prof
from rx.analysis import aerobic
from rx.prescribe import engine as eng
from rx.store import Store

TODAY = date(2026, 10, 12)


@pytest.fixture(scope="module")
def p(tmp_path_factory):
    s = Store(tmp_path_factory.mktemp("c") / "t.db")
    demo.seed(s, days=120, end=TODAY)
    return prof.build(s, TODAY.isoformat())


def test_drift_separates_below_and_above_aerobic_threshold():
    rng = random.Random(3)
    t, h, v = demo.steady_run(50, 138, 1.0, rng)
    below = aerobic.steady_session("a", "2026-10-01", "run", "Run", t, h, v)
    t, h, v = demo.steady_run(50, 152, 1.0, rng)
    above = aerobic.steady_session("b", "2026-10-02", "run", "Run", t, h, v)
    assert below.drift_pct < 5 < above.drift_pct


def test_mountain_bike_speed_is_not_used():
    t, h, v = demo.steady_run(50, 138, 1.0, random.Random(1))
    assert aerobic.steady_session("a", "2026-10-01", "bike", "MountainBikeRide", t, h, v) is None


def test_aerobic_threshold_and_measured_zones(p):
    assert abs(p.aerobic.bpm - demo.TRUE_LT1) <= 5
    assert p.zones.measured
    assert p.zones.top("Z2 Aerobic") == p.aerobic.bpm
    assert p.zones.top("Z4 Threshold") == p.threshold.bpm


def test_progress_detects_improving_efficiency(p):
    eff = [m for m in p.progress.measures if m.name.startswith("Aerobic efficiency (run)")][0]
    assert eff.trend == "improving"


def test_personal_response_is_learned(p):
    r = p.response
    assert r.fitted and r.slope < 0          # bigger days → lower HRV next morning
    assert r.recovery_days is not None


def test_every_work_day_has_ideal_flex_skip_overdo(p):
    rx = eng.build_rx(p)
    assert rx.verdict in (eng.EASY, eng.STEADY, eng.HARD)
    assert rx.compress and rx.stretch
    assert "fitness" in rx.if_skip
    assert str(rx.hr_ceiling) in rx.if_overdo
    assert "tomorrow" in rx.if_overdo
