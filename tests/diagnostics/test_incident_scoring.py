import json

from quantum_link_optimizer.diagnostics.evaluation import score_trial, summarize


def write_trial(path, incidents, network=None):
    path.mkdir()
    sender = [{"event": "probe_result", "time_ns": 1_000_000_000}]
    sender += [
        {"event": "incident_opened", "time_ns": t, "state": state, "scope": scope}
        for t, state, scope in incidents
    ]
    truth = [{"event": "fault", "time_ns": 2_000_000_000}]
    config = {
        "target_ms": 100,
        "network": network
        or {
            "A": {"forward": {"delay_ms": 30}, "reverse": {"delay_ms": 30}},
            "B": {"forward": {"delay_ms": 20}, "reverse": {"delay_ms": 20}},
        },
    }
    (path / "sender.jsonl").write_text("\n".join(map(json.dumps, sender)))
    (path / "truth.jsonl").write_text("\n".join(map(json.dumps, truth)))
    (path / "scenario.json").write_text(json.dumps(config))


def test_swap_needs_both_paths_after_their_onsets(tmp_path):
    write_trial(
        tmp_path / "swap",
        [
            (1_100_000_000, "branch_degradation", "A"),
            (2_200_000_000, "branch_degradation", "B"),
            (2_300_000_000, "receiver_problem", "A"),
        ],
    )
    row = score_trial(tmp_path / "swap", "swap")

    assert [e["detected"] for e in row["expected"]] == [True, True]
    assert row["expected"][1]["time_to_detect_ms"] == 200
    assert row["false_alarms"] == 1


def test_normal_counts_every_incident_except_slow_by_design(tmp_path):
    slow_b = {
        "A": {"forward": {"delay_ms": 30}, "reverse": {"delay_ms": 30}},
        "B": {"forward": {"delay_ms": 45}, "reverse": {"delay_ms": 45}},
    }
    write_trial(
        tmp_path / "normal",
        [
            (1_100_000_000, "branch_degradation", "B"),
            (1_200_000_000, "receiver_problem", "A"),
            (1_300_000_000, "telemetry_missing", "system"),
        ],
        slow_b,
    )
    row = score_trial(tmp_path / "normal", "normal")
    summary = summarize([row])

    assert row["false_alarms"] == 1
    assert row["telemetry_alarms"] == 1
    assert summary["detection_rate"] is None
