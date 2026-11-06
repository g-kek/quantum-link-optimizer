import time
from types import SimpleNamespace

import pytest

from quantum_link_optimizer.selection.config import Settings
from quantum_link_optimizer.selection.diagnostics import diagnose
from quantum_link_optimizer.selection.state import Observations


def observed_state(events=(), age_ms=0):
    observer = Observations(Settings(max_age_ms=100))
    for event, fields in events:
        observer.observe(event, **fields)
    now = time.monotonic_ns() + int(age_ms * 1e6)
    senders = {
        path: SimpleNamespace(session=SimpleNamespace(phase=lambda: "steady"))
        for path in ("A", "B")
    }
    return observer.snapshot(now, senders)


def test_local_sender_queue_is_observed_without_claiming_network_cause():
    findings = diagnose(observed_state(), local_queue_depth=3, max_age_ms=100)
    sender = next(f for f in findings if f["scope"] == "sender")
    assert sender["queue_depth"] == 3
    assert sender["cause"] == "local_wait_observed"
    assert "slow_kms" in sender["alternatives"]
    assert sender["age_ms"] == 0
    assert sender["status"] != "confirmed"


@pytest.mark.parametrize("path", ["A", "B"])
@pytest.mark.parametrize(
    "event,fields,evidence",
    [
        ("sender_queue_sample", {"queue_depth": 3}, "sender_queue"),
        (
            "session_ready",
            {"phase": "startup", "key_wait_ns": 20_000_000},
            "observed_key_wait",
        ),
        (
            "ack_received",
            {"receiver_queue_wait_ns": 2_000_000},
            "receiver_queue_wait_ns",
        ),
        (
            "ack_received",
            {"receiver_processing_ns": 2_000_000},
            "receiver_processing_ns",
        ),
        ("ack_received", {"receiver_key_wait_ns": 2_000_000}, "receiver_key_wait_ns"),
    ],
)
def test_path_evidence_is_scoped_and_expires(path, event, fields, evidence):
    events = [(event, {"path": path, **fields})]
    fresh = diagnose(observed_state(events), max_age_ms=100)
    finding = next(f for f in fresh if f["scope"] == path)
    assert evidence in finding["evidence"]
    assert finding["cause"] == "not_established"
    assert finding["alternatives"]
    assert any(age is not None for age in finding["evidence_age_ms"].values())
    assert not any(f["scope"] == ("B" if path == "A" else "A") for f in fresh)
    stale = diagnose(observed_state(events, age_ms=200), max_age_ms=100)
    assert len(stale) == 1
    assert stale[0]["status"] == "insufficient_evidence"
    assert stale[0]["cause"] == "not_established"


def test_missing_measurements_do_not_prove_health_or_a_shared_failure():
    findings = diagnose(observed_state(), shared_segment=True, max_age_ms=100)
    assert len(findings) == 1
    assert findings[0]["status"] == "insufficient_evidence"
    assert findings[0]["cause"] == "not_established"


@pytest.mark.parametrize("shared", [False, True])
def test_simultaneous_slow_paths_remain_ambiguous(shared):
    events = [
        ("probe_result", {"path": path, "status": "ok", "rtt_ms": 700})
        for path in ("A", "B")
    ]
    findings = diagnose(observed_state(events), shared_segment=shared)
    assert len(findings) == 1
    assert findings[0]["status"] == "candidate"
    assert findings[0]["known_shared_segment"] == shared
    assert "independent_path_problems" in findings[0]["alternatives"]
