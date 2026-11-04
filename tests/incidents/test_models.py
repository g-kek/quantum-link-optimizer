import pytest

from quantum_link_optimizer.incidents.clock import BoundaryClock
from quantum_link_optimizer.incidents.device import Device
from quantum_link_optimizer.incidents.profiles import NAMES, profile, validate


def test_rate_queue_errors_and_outage_recovery():
    device = Device({"rate_mbit": 0.008, "queue_limit_bytes": 200})
    assert device.transit(100, "forward", 0) == pytest.approx(0.1)
    assert device.transit(100, "forward", 0) == pytest.approx(0.2)
    assert device.transit(1, "forward", 0) is None
    assert device.snapshot(0)["queue_drops"] == 1
    generation = device.generation
    device.update({"available": False})
    device.update({"available": True})
    assert not device.valid_delivery(generation)
    assert device.snapshot(0)["queued_bytes"] == 0
    device.update({"loss_pct": 100})
    assert device.transit(100, "reverse", 0) is None
    assert device.counters["error_drops"] == 1


def test_holdover_continuity_budget_and_relock():
    clock = BoundaryClock()
    clock.update(
        {"state": "holdover", "drift_ppb": 500, "uncertainty_growth_ns_per_s": 500}, 1
    )
    assert clock.truth(2)["actual_offset_ns"] == 500
    assert clock.truth(4)["within_budget"] is False
    assert clock.report(4)["reported_usable"] is False
    clock.update({"source": "gm-2", "state": "relocking", "drift_ppb": 0}, 4)
    assert clock.truth(5)["actual_offset_ns"] == 1500
    assert clock.report(5)["source_changes"] == 1
    clock.update(
        {
            "state": "locked",
            "offset_ns": 0,
            "uncertainty_ns": 50,
            "uncertainty_growth_ns_per_s": 0,
        },
        6,
    )
    assert clock.report(7)["reported_usable"] is True


def test_monitor_may_be_wrong_without_hiding_ground_truth():
    clock = BoundaryClock(settings={"offset_ns": 50000, "measurement_bias_ns": -50000})
    assert clock.report(0)["reported_usable"] is True
    assert clock.report(0)["offset_estimate_ns"] == 0
    assert clock.truth(0)["within_budget"] is False


@pytest.mark.parametrize("name", sorted(NAMES))
def test_profiles_have_valid_explicit_assumptions(name):
    spec = profile(name, 6000)
    validate(spec, 6000)
    assert all(item["status"] == "assumption" for item in spec["assumptions"])
    assert spec["clocks"]["A"]["budget_ns"] == 1000


@pytest.mark.parametrize(
    "change", [{"drift_ppb": float("nan")}, {"state": "unknown"}, {"budget_ns": 0}]
)
def test_invalid_clock_settings_are_rejected(change):
    with pytest.raises(ValueError):
        BoundaryClock(settings=change)


def test_assumptions_and_shared_settings_cannot_be_silently_omitted():
    spec = profile("equipment-shared-outage", 100)
    spec["assumptions"] = []
    with pytest.raises(ValueError, match="assumptions"):
        validate(spec, 100)
    spec = profile("equipment-shared-outage", 100)
    spec["devices"]["B"]["rate_mbit"] = 10
    with pytest.raises(ValueError, match="conflicting"):
        validate(spec, 100)
