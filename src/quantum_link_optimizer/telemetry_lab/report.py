import json


def records(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def summarize(output):
    sender = records(output / "sender.jsonl")
    receiver = records(output / "receiver.jsonl")
    submitted = {
        event["command_id"] for event in sender if event["event"] == "command_submitted"
    }
    completed = [event for event in sender if event["event"] == "command_completed"]
    phases = {}
    for phase in ("startup", "steady", "rotation"):
        ready = [
            event
            for event in sender
            if event["event"] == "session_ready" and event["phase"] == phase
        ]
        phases[phase] = {
            "count": len(ready),
            "key_wait_ns": [event["key_wait_ns"] for event in ready],
        }
    executed = {
        event["command_id"]
        for event in receiver
        if event["event"] == "command_result"
        and event["status"] == "executed"
        and not event.get("duplicate")
    }
    config = json.loads((output / "scenario.json").read_text())
    expected = config.get("count", config.get("commands", {}).get("count"))
    result = {
        "expected_commands": expected,
        "missing_submissions": max(0, expected - len(submitted))
        if expected is not None
        else None,
        "submitted": len(submitted),
        "completed": len(completed),
        "missing_completion": sorted(
            submitted - {event["command_id"] for event in completed}
        ),
        "timely_executed": sum(
            event.get("status") == "executed" and event.get("timely", False)
            for event in completed
        ),
        "receiver_executions": len(executed),
        "phases": phases,
        "attempts": sum(event["event"] == "attempt_sent" for event in sender),
        "ack_rtt_ns": [
            event["rtt_ns"]
            for event in sender
            if event["event"] == "ack_received" and event.get("rtt_ns") is not None
        ],
        "unknown_outcomes": sum(
            event.get("outcome") == "unknown" for event in completed
        ),
        "missing_logs": [
            name
            for name in ("sender.jsonl", "receiver.jsonl", "kms.jsonl")
            if not (output / name).exists()
        ],
        "statuses": {
            status: sum(event["status"] == status for event in completed)
            for status in sorted({event["status"] for event in completed})
        },
    }
    (output / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    (output / "summary.md").write_text(
        "# Опыт\n\n"
        f"- Поступило: {result['submitted']}\n"
        f"- Завершено: {result['completed']}\n"
        f"- Исполнено на receiver: {result['receiver_executions']}\n"
        f"- ACK до дедлайна: {result['timely_executed']}\n"
        f"- Без завершения: {len(result['missing_completion'])}\n\n"
        "Статусы: `" + json.dumps(result["statuses"], ensure_ascii=False) + "`\n"
    )
    return result
