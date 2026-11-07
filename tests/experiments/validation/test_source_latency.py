import json

from quantum_link_optimizer.experiments.validation.metrics import trial_metrics


def test_generator_lag_cannot_be_hidden_by_a_fast_agent_timestamp(tmp_path):
    events = [
        {
            "event": "workload_arrival",
            "command_id": "one",
            "available_ns": 1_000_000_000,
            "schedule_lag_ns": 200_000_000,
        },
        {
            "event": "command_completed",
            "command_id": "one",
            "available_ns": 1_050_000_000,
            "elapsed_ns": 10_000_000,
            "status": "executed",
        },
    ]
    (tmp_path / "sender.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in events)
    )
    (tmp_path / "receiver.jsonl").write_text("")
    (tmp_path / "samples.jsonl").write_text("")
    (tmp_path / "scenario.json").write_text('{"target_ms":100}')
    (tmp_path / "resource-usage.json").write_text('{"scope":"unit fixture"}')
    summary = {"offered": 1, "timely_executed": 1, "timely_fraction": 1}
    measured = trial_metrics(tmp_path, summary, "normal")

    assert measured["source_latency_ms"]["p99"] == 250
    assert measured["timely_executed"] == 0
    assert measured["timely_fraction"] == 0
    assert measured["end_to_end_ms"]["p99"] == 10
