import random

from rx.analysis import hr
from rx.demo import hr_session


def test_spike_is_rejected_and_true_peak_found():
    t, h = hr_session(30, 130, [(15, 5, 178)], random.Random(1))
    h[100] = 225          # single-sample optical glitch
    h[101] = 224
    p = hr.credible_peak("a", "2026-10-01", "bike", t, h)
    assert p.credible
    assert 172 <= p.bpm <= 180


def test_jump_without_ramp_is_rejected():
    t = list(range(1800))
    h = [110.0] * 1800
    for i in range(900, 930):
        h[i] = 195.0       # sudden plateau, no climb: sensor lock-on, not physiology
    p = hr.credible_peak("a", "2026-10-01", "run", t, h)
    assert not p.credible


def test_cadence_lock_is_rejected():
    t = list(range(1200))
    rng = random.Random(2)
    cad = [88 + rng.gauss(0, 0.5) for _ in t]
    h = [c * 2 for c in cad]   # wrist sensor reporting step rate (176 spm)
    for i in range(300):       # give it a plausible ramp
        h[i] = 120 + i * 0.19
    p = hr.credible_peak("a", "2026-10-01", "run", t, h, cad)
    assert not p.credible and "cadence" in p.reason


def test_max_hr_confirmation():
    peaks = [hr.Peak("a", "2026-09-01", "bike", 181, True),
             hr.Peak("b", "2026-09-20", "run", 179, True),
             hr.Peak("c", "2026-09-21", "run", 199, False, "jumped")]
    m = hr.estimate_max_hr(peaks)
    assert m.bpm == 181 and m.confirmed
    single = hr.estimate_max_hr([hr.Peak("a", "2026-09-01", "bike", 181, True),
                                 hr.Peak("b", "2026-09-20", "run", 168, True)])
    assert single.bpm == 181 and not single.confirmed


def test_threshold_and_zones():
    th = hr.estimate_threshold([("2026-09-01", 170.0, None), ("2026-09-08", 168.0, 163.0)])
    assert th.bpm == 163 or th.bpm == round(170 * 0.95)
    z = hr.zones_from_lthr(165, 182)
    assert z.top("Z2 Aerobic") < z.bottom("Z3 Tempo") <= z.top("Z3 Tempo") < z.bottom("Z4 Threshold")
    assert z.bounds[-1][2] == 182
