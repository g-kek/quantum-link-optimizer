import asyncio
import time
from types import SimpleNamespace

from quantum_link_optimizer.diagnostics.engine import IncidentTracker
from quantum_link_optimizer.diagnostics.rules import assess
from quantum_link_optimizer.selection.config import Settings
from quantum_link_optimizer.selection.router import Router
from quantum_link_optimizer.selection.state import Observations


def senders():
    return {
        p: SimpleNamespace(
            session=SimpleNamespace(logical_id="same", phase=lambda: "steady")
        )
        for p in ("A", "B")
    }


def snapshot(a=700, b=60):
    observer = Observations(Settings())

    for path, rtt in (("A", a), ("B", b)):
        observer.observe("probe_result", path=path, status="ok", rtt_ms=rtt)

    return observer.snapshot(time.monotonic_ns(), senders())


def test_single_branch_and_shared_slowdown_are_scoped_without_exact_root_cause():
    one = assess(snapshot())
    assert [(f["state"], f["scope"]) for f in one] == [("branch_degradation", "A")]
    assert one[0]["measurements"][0]["value"] == 700
    assert one[0]["physical_onset_ns"] is None
    assert one[0]["alternatives"]
    both = assess(snapshot(700, 700), shared_segment=True)
    assert both[0]["scope"] == "A+B"
    assert both[0]["status"] == "candidate"
    assert "independent_path_problems" in both[0]["alternatives"]


def test_empty_shared_pool_uses_fresh_api_observations_once():
    rows = snapshot(60, 60)

    for row in rows.values():
        row.update(kms_pool="shared", kms_available_keys=0, kms_observation_age_ms=0)

    findings = assess(rows)
    assert len(findings) == 1
    assert findings[0]["state"] == "key_pool_empty"
    assert findings[0]["scope"] == "KMS"

    for row in rows.values():
        row["kms_observation_age_ms"] = 3000

    findings = assess(rows)
    assert all(f["state"] == "telemetry_stale" for f in findings)


def test_stale_telemetry_never_resolves_the_previous_fault_as_recovery():
    events = []
    tracker = IncidentTracker(lambda event, **f: events.append((event, f)), 100)
    rows = snapshot()
    first = tracker.update(rows, 10_000_000_000)[0]
    original = first["incident_id"]

    for row in rows.values():
        row.update(observation_age_ms=200, ewma_rtt_ms=None)

    findings = tracker.update(rows, 10_200_000_000)
    assert any(
        f["incident_id"] == original and f["lifecycle"] == "unobserved"
        for f in findings
    )
    assert not any(e == "incident_resolved" for e, _ in events)

    tracker.update(snapshot(60, 60), 10_300_000_000)
    closed = [
        f
        for e, f in events
        if e == "incident_resolved" and f["incident_id"] == original
    ]
    assert len(closed) == 1
    assert closed[0]["first_observed_ns"] == first["first_observed_ns"]
    assert closed[0]["resolved_ns"] == 10_300_000_000


def test_receiver_problem_and_local_overload_have_numeric_evidence():
    rows = snapshot(60, 60)
    rows["B"].update(receiver_queue_wait_ns=20_000_000, receiver_observation_age_ms=0)
    findings = assess(rows, local_queue_depth=3, admission_rejections=2)
    assert {f["state"] for f in findings} == {"receiver_problem", "local_overload"}
    assert all(f["measurements"] and f["alternatives"] for f in findings)


def test_monitor_detects_ongoing_wait_and_staleness_without_any_route_decision():
    async def exercise():
        events = []
        router = Router(
            senders(),
            Settings(max_age_ms=50, diagnostic_interval_ms=5),
            lambda event, **f: events.append((event, f)),
        )

        try:
            for path in ("A", "B"):
                router.observe("probe_result", path=path, status="ok", rtt_ms=60)

            router.observe("key_wait_started", path="A", command_id="waiting")
            await asyncio.sleep(0.03)
            waits = [
                f
                for e, f in events
                if e == "incident_opened" and f["state"] == "kms_delay"
            ]
            assert waits and waits[0]["scope"] == "A"
            assert any(
                m["metric"] == "active_key_wait_ms" for m in waits[0]["measurements"]
            )
            assert not any(e == "route_decision" for e, _ in events)
            router.observe(
                "session_ready",
                path="A",
                command_id="waiting",
                phase="startup",
                key_wait_ns=0,
            )
            await asyncio.sleep(0.04)
            assert any(
                e == "incident_opened" and f["state"] == "telemetry_stale"
                for e, f in events
            )
        finally:
            await router.close()

        assert router.monitor.done()

    asyncio.run(exercise())
