import json

from quantum_link_optimizer.analysis.waterfall import breakdown


def write_trial(path, commands):
    events = []

    for index, (queue_ms, network_ms) in enumerate(commands):
        command = f"command-{index}"
        elapsed = queue_ms + 2 + network_ms
        events += [
            {"event": "workload_arrival", "command_id": command, "schedule_lag_ns": 0},
            {
                "event": "route_decision",
                "command_id": command,
                "queue_wait_ns": int(queue_ms * 1e6),
            },
            {
                "event": "session_ready",
                "command_id": command,
                "path": "A",
                "key_wait_ns": int(1e6),
            },
            {
                "event": "attempt_sent",
                "command_id": command,
                "path": "A",
                "sender_send_processing_ns": 0,
            },
            {
                "event": "command_completed",
                "command_id": command,
                "path": "A",
                "status": "executed",
                "elapsed_ns": int(elapsed * 1e6),
                "receiver_processing_ns": int(1e6),
            },
        ]

    path.mkdir()
    (path / "sender.jsonl").write_text(
        "\n".join(json.dumps(event) for event in events), encoding="utf-8"
    )


def test_tail_is_attributed_to_the_growing_queue(tmp_path):
    trial = tmp_path / "trial"
    write_trial(trial, [(20 * index, 60) for index in range(20)])
    result = breakdown(trial)
    stages = {part["stage"]: part["mean_ms"] for part in result["tail"]["stages"]}

    assert result["largest_tail_stage"] == "Очередь и pacing"
    assert stages["router_queue"] == 380
    assert stages["network"] == 60
    assert stages["key_wait"] == 1
    assert len(result["commands"]) == 20
