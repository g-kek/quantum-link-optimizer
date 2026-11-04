import asyncio

import pytest

from quantum_link_optimizer.collection.features import rows
from quantum_link_optimizer.incidents.features import ComponentHistory
from quantum_link_optimizer.incidents.profiles import profile
from quantum_link_optimizer.incidents.runtime import Runtime


def report(generated, available, offset):
    return {
        "event": "component_observation",
        "generated_ns": generated,
        "algorithm_available_ns": available,
        "available_ns": available,
        "path": "A",
        "device": None,
        "clock": {
            "state": "locked",
            "offset_estimate_ns": offset,
            "uncertainty_ns": 50,
            "reported_usable": True,
            "source_changes": 0,
        },
    }


def test_old_delivered_reports_do_not_refresh_age_or_replace_newer_measurements():
    history = ComponentHistory(max_age_ms=1)
    history.observe(report(100, 200, 10))
    history.observe(report(50, 300, 999))
    values, _ = history.at("A", 400)
    assert values["bc_offset_estimate_ns"] == 10
    assert values["bc_observation_age_ms"] == pytest.approx(0.0003)
    values, _ = history.at("A", 2_000_000)
    assert values["bc_stale"] == 1
    assert values["bc_offset_estimate_ns"] is None
    assert values["bc_missing"] == 0


def test_unavailable_reports_and_truth_are_not_features():
    event = report(0, 20, 50000) | {"record_id": 2}
    decision = {
        "event": "decision",
        "available_ns": 10,
        "algorithm_available_ns": 10,
        "record_id": 1,
        "sample": 0,
    }
    truth = [
        {
            "event": "incident_state",
            "sample": 0,
            "path": "A",
            "available_ns": 5,
            "device_available": True,
            "clock": {"actual_offset_ns": 90000, "within_budget": False},
        }
    ]
    config = {"episode_id": "test", "split": "train", "target_ms": 100, "max_age_ms": 1}
    row = next(rows(config, [event, decision], truth))
    assert row["features"]["bc_offset_estimate_ns"] is None
    assert row["targets"]["bc_actual_offset_ns_at_decision"] == 90000
    assert "actual_offset_ns" not in str(row["features"])


def test_common_component_and_observation_delay():
    async def exercise():
        spec = profile("equipment-shared-outage", 10)
        events, truth = [], []
        runtime = Runtime(
            spec,
            10,
            1,
            lambda name, **fields: events.append((name, fields)),
            lambda name, **fields: truth.append((name, fields)),
        )
        try:
            assert runtime.devices["A"] is runtime.devices["B"]
            runtime.tick(2)
            assert runtime.devices["B"].settings["available"] is False
            assert not events
            await asyncio.sleep(0.18)
            assert len(events) == 2
            assert all(event[1]["device"]["available"] is False for event in events)
        finally:
            runtime.close()

    asyncio.run(exercise())


def test_future_timestamp_is_rejected():
    with pytest.raises(ValueError, match="future"):
        ComponentHistory(1).observe(report(300, 200, 0))


def test_monitor_gap_retains_measurement_age_and_shared_clock_scope():
    async def exercise():
        events = []
        spec = profile("bc-common-loss", 10)
        runtime = Runtime(
            spec,
            10,
            1,
            lambda name, **fields: events.append(fields),
            lambda *args, **fields: None,
        )
        try:
            assert runtime.clocks["A"] is runtime.clocks["B"]
            runtime.tick(2)
            assert runtime.clocks["B"].report(runtime.loop.time())["state"] == "freerun"
            runtime.close()
            await asyncio.sleep(0.18)
            assert not events
        finally:
            runtime.close()

    asyncio.run(exercise())


def test_component_snapshot_cannot_be_read_before_delivery():
    history = ComponentHistory(1)
    history.observe(report(100, 300, 10))
    with pytest.raises(ValueError, match="not available"):
        history.at("A", 200)
