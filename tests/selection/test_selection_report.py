import json

from quantum_link_optimizer.selection.report import report


def test_copy_bytes_do_not_depend_on_which_path_sends_first(tmp_path):
    events = [
        {"event": "leg_started", "command_id": "x", "path": "A", "extra": False},
        {"event": "leg_started", "command_id": "x", "path": "B", "extra": True},
        {"event": "attempt_sent", "command_id": "x", "path": "B", "wire_bytes": 100},
        {"event": "attempt_sent", "command_id": "x", "path": "A", "wire_bytes": 120},
    ]
    (tmp_path / "sender.jsonl").write_text(
        "\n".join(json.dumps(event) for event in events) + "\n"
    )
    (tmp_path / "receiver.jsonl").write_text("")
    result = report(tmp_path, 100)
    assert result["extra_path_bytes"] == 100
    assert result["wire_bytes"] == 220
