import numpy as np

from quantum_link_optimizer.diagnostics.baseline import Norm
from quantum_link_optimizer.diagnostics.changes import StepDetector
from quantum_link_optimizer.diagnostics.rules import assess


def test_norm_waits_for_warmup_and_ignores_faults():
    norm = Norm(soft_floor=1.0, hard_limit=20.0, warmup=5)

    assert norm.threshold() == 20.0

    for index, value in enumerate([2.0, 2.1, 1.9, 2.2, 2.0, 500.0]):
        norm.observe(value, index)

    assert norm.state()["samples"] == 5
    assert 2.2 < norm.threshold() < 20.0


def test_norm_keeps_the_upper_level_of_a_two_level_signal():
    norm = Norm(soft_floor=1.0, hard_limit=20.0, warmup=20)

    for index in range(200):
        # Every fourth command fetches a new key at the receiver
        norm.observe(3.0 if index % 4 == 0 else 0.01, index)

    assert norm.threshold() >= 4.5


def test_same_observation_is_counted_once():
    norm = Norm(soft_floor=1.0, hard_limit=20.0)
    norm.observe(2.0, 10)
    norm.observe(2.0, 10)

    assert norm.state()["samples"] == 1


def test_step_detector_finds_the_swap_and_ignores_noise():
    rng = np.random.default_rng(1)
    detector = StepDetector()
    swap = np.r_[160 + rng.normal(0, 3, 40), 40 + rng.normal(0, 3, 40)]
    steps = [s for v in swap if (s := detector.update(float(v)))]

    assert len(steps) == 1
    assert steps[0]["before_ms"] > 150 and steps[0]["after_ms"] < 50

    detector = StepDetector()
    noise = 60 + rng.exponential(5, 300)

    assert not [s for v in noise if detector.update(float(v))]


def row(**fields):
    base = {
        "observation_age_ms": 10,
        "ewma_rtt_ms": 40,
        "consecutive_timeouts": 0,
    }
    return {**base, **fields}


def test_receiver_threshold_comes_from_the_norm():
    snapshot = {
        "A": row(receiver_observation_age_ms=5, receiver_key_wait_ns=3_000_000),
        "B": row(),
    }
    states = {f["state"] for f in assess(snapshot, now_ns=10**12)}

    assert "receiver_problem" in states

    thresholds = {"receiver_key_wait_ns": 5_000_000}
    states = {
        f["state"] for f in assess(snapshot, now_ns=10**12, thresholds=thresholds)
    }

    assert "receiver_problem" not in states


def test_only_a_step_up_opens_a_path_change():
    up = {"before_ms": 40, "after_ms": 160}
    down = {"before_ms": 160, "after_ms": 40}

    for step, expected in ((up, True), (down, False)):
        snapshot = {"A": row(rtt_step=step, rtt_step_age_ms=5), "B": row()}
        findings = assess(snapshot, now_ns=10**12)

        assert any(f["state"] == "path_change" for f in findings) is expected


def test_findings_carry_class_and_domain():
    snapshot = {"A": row(ewma_rtt_ms=300), "B": row()}
    finding = next(
        f for f in assess(snapshot, now_ns=10**12) if f["state"] == "branch_degradation"
    )

    assert finding["class"] == "path_degradation"
    assert finding["domain"] == "network"
    assert finding["action"]
