import time
from types import SimpleNamespace

from quantum_link_optimizer.selection.config import Settings
from quantum_link_optimizer.selection.diagnostics import diagnose
from quantum_link_optimizer.selection.policy import Policy
from quantum_link_optimizer.selection.state import Observations


def snapshot(settings, a=60, b=55):
    observer = Observations(settings)
    for path, rtt in (("A", a), ("B", b)):
        observer.observe("probe_result", path=path, status="ok", rtt_ms=rtt, sample=0)
    senders = {
        path: SimpleNamespace(session=SimpleNamespace(phase=lambda: "steady"))
        for path in ("A", "B")
    }
    return observer, senders


def test_hold_hysteresis_and_emergency_override():
    settings = Settings(min_hold_ms=500, switch_margin_ms=10)
    observer, senders = snapshot(settings)
    now = time.monotonic_ns()
    state = observer.snapshot(now, senders)
    policy = Policy(settings)
    assert policy.choose(state, now) == ("A", "hold")
    state["B"]["ewma_rtt_ms"] = 30
    assert policy.choose(state, now + 10_000_000) == ("A", "hold")
    assert policy.choose(state, now + 600_000_000) == ("B", "better_path")
    state["A"]["ewma_rtt_ms"] = 25
    assert policy.choose(state, now + 1_200_000_000) == ("B", "hysteresis")
    state["B"]["device_available"] = False
    assert policy.choose(state, now + 1_210_000_000) == ("A", "emergency")


def test_common_slowdown_disables_hedging_and_oscillation():
    settings = Settings(shared_segment=True, min_hold_ms=0)
    observer, senders = snapshot(settings, 700, 500)
    now = time.monotonic_ns()
    state = observer.snapshot(now, senders)
    policy = Policy(settings)
    assert policy.choose(state, now) == ("A", "common_slowdown")
    assert policy.alternative("A", state)[0] is None
    findings = diagnose(state, shared_segment=True)
    assert any(
        finding["cause"] == "shared_or_parallel_slowdown" for finding in findings
    )
    assert all(finding["status"] != "confirmed" for finding in findings)


def test_clock_requirement_and_known_kms_failure():
    settings = Settings(require_clock=True, min_hold_ms=0)
    observer, senders = snapshot(settings, 700, 60)
    state = observer.snapshot(time.monotonic_ns(), senders)
    assert Policy(settings).choose(state, time.monotonic_ns())[0] is None
    state["B"]["bc_reported_usable"] = True
    assert Policy(settings).choose(state, time.monotonic_ns())[0] == "B"
    state["B"]["kms_blocked"] = True
    assert Policy(settings).choose(state, time.monotonic_ns())[0] is None


def test_future_observation_rejected_and_stale_not_used():
    settings = Settings(max_age_ms=1)
    observer, senders = snapshot(settings)
    state = observer.snapshot(time.monotonic_ns() + 2_000_000, senders)
    assert all(row["ewma_rtt_ms"] is None for row in state.values())
    assert Policy(settings).alternative("A", state)[1] == "alternative_unobserved"
