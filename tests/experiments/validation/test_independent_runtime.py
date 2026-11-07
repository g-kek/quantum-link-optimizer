import asyncio

import pytest

from quantum_link_optimizer.experiments.runtime import run_trial
from quantum_link_optimizer.experiments.scenarios import configuration
from quantum_link_optimizer.experiments.validation.benchmark import payload_contract
from quantum_link_optimizer.experiments.validation.metrics import trial_metrics
from quantum_link_optimizer.selection.config import Settings
from quantum_link_optimizer.telemetry_lab.manifest import verify_files
from quantum_link_optimizer.telemetry_lab.report import records


@pytest.mark.parametrize("case,bypass", [("receiver-restart", False), ("normal", True)])
def test_restart_and_protected_bypass_preserve_all_arrivals(tmp_path, case, bypass):
    config = configuration(case, 1009, count=6)
    config.update(bypass=bypass, logging_mode="deferred")
    path = tmp_path / "trial"
    summary = asyncio.run(run_trial(config, path, Settings(mode="fixed-b")))
    measured = trial_metrics(path, summary, case)
    assert summary["offered"] == summary["completed"] == 6
    assert summary["duplicate_executions"] == 0
    assert measured["source_latency_ms"]["count"] == 6
    assert all(verify_files(path).values())

    if case == "receiver-restart":
        restart = next(
            e for e in records(path / "truth.jsonl") if e["event"] == "receiver_restart"
        )
        assert restart["executions_before"] == restart["executions_after"]
        assert restart["executions_before"] > 0
    else:
        assert measured["decision_us"]["count"] == 0


def test_application_bytes_are_not_silently_relabelled_as_udp_bytes():
    result = payload_contract()
    assert result["status"] == "unsupported"
    assert result["requested_application_bytes"] == 1024
    assert "1200" in result["evidence"]
