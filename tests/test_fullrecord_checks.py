"""Coach page: the consistency checks on raw readings, on synthetic sessions."""
import numpy as np
import pytest

pytest.importorskip("fitparse")

from fullrecord.checks import check_record, credible_peak  # noqa: E402
from fullrecord.fit import RECORD_FIELDS  # noqa: E402


def run(seconds=1800, hr=140.0, spm=170.0, speed=2.8):
    t = np.arange(seconds, dtype=float)
    rec = {k: np.full(len(t), np.nan) for k in RECORD_FIELDS}
    rec["t"] = t
    rec["hr"] = hr + 3 * np.sin(t / 40)
    rec["cad"] = np.full(len(t), spm / 2)          # FIT running cadence is strides per minute
    rec["speed"] = np.full(len(t), speed)
    rec["dist"] = np.cumsum(rec["speed"])
    rec["cad_scale"] = 2
    return rec


def test_clean_run_is_untouched():
    rec = run()
    assert check_record(rec, "foot") == []
    assert np.isfinite(rec["hr"]).all()


def test_cadence_lock_is_left_out():
    rec = run()
    rec["hr"][600:1200] = 170 + np.random.default_rng(1).normal(0, 1, 600)   # a jump to cadence, pace unchanged
    notes = check_record(rec, "foot")
    assert any("jumped up to the cadence" in n for n in notes)
    assert np.isnan(rec["hr"][700:1100]).all()
    assert np.isfinite(rec["hr"][:500]).all()


def test_spike_is_left_out():
    rec = run()
    rec["hr"][900] = 230
    notes = check_record(rec, "foot")
    assert any("jumped" in n for n in notes)
    assert np.isnan(rec["hr"][900])


def test_stuck_sensor_is_left_out():
    rec = run()
    rec["hr"][300:700] = 151
    notes = check_record(rec, "foot")
    assert any("did not change" in n for n in notes)


def test_peak_without_ramp_is_not_a_peak():
    rec = run(hr=120)
    rec["hr"][1000:1070] = 200          # a plateau with no rise into it
    rec["hr"][1400:1600] = np.linspace(120, 165, 200)   # a real climb to a peak
    rec["hr"][1600:1700] = 165
    peak, notes = credible_peak(rec, 60)
    assert peak is not None and 160 <= peak <= 166
    assert any("no rise into it" in n for n in notes)


def test_cadence_match_reached_with_effort_is_kept():
    rec = run()
    rec["hr"][600:1200] = np.linspace(140, 170, 600)
    rec["hr"][1200:1500] = 170
    rec["speed"][600:] = np.linspace(2.8, 3.6, 1200)
    rec["dist"] = np.cumsum(rec["speed"])
    notes = check_record(rec, "foot")
    assert not any("left out" in n and "cadence" in n for n in notes)
