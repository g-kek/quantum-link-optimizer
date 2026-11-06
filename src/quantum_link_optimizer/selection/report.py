import json

from quantum_link_optimizer.telemetry_lab.report import records


def report(output, target_ms):
    sender = records(output / "sender.jsonl")
    receiver = records(output / "receiver.jsonl")
    complete = [event for event in sender if event["event"] == "command_completed"]
    offered = [event for event in sender if event["event"] == "command_submitted"]
    routes = [event for event in sender if event["event"] == "route_decision"]
    extra = set()
    extra_bytes = 0
    wire_bytes = 0
    for event in sender:
        if event["event"] == "leg_started" and event["extra"]:
            extra.add((event["command_id"], event["path"]))
        if event["event"] != "attempt_sent":
            continue
        command_id = event["command_id"]
        wire_bytes += event["wire_bytes"]
        if (command_id, event["path"]) in extra:
            extra_bytes += event["wire_bytes"]
    applied = [
        event
        for event in receiver
        if event["event"] == "command_result"
        and event["status"] == "executed"
        and not event["duplicate"]
    ]
    success = sum(
        event["status"] == "executed" and event.get("elapsed_ns", 0) <= target_ms * 1e6
        for event in complete
    )
    paths = [event["path"] for event in routes if event["path"]]
    result = {
        "offered": len(offered),
        "completed": len(complete),
        "missing_completion": len(offered) - len(complete),
        "timely_executed": success,
        "timely_fraction": success / len(offered) if offered else None,
        "executions": len(applied),
        "duplicate_executions": len(applied)
        - len(
            {
                (event.get("logical_session", event["session"]), event["command_id"])
                for event in applied
            }
        ),
        "switches": sum(
            left != right for left, right in zip(paths, paths[1:], strict=False)
        ),
        "keys_delivered": len(
            {
                event["key_id"]
                for event in records(output / "kms.jsonl")
                if event["event"] == "kms_delivered"
            }
        ),
        "key_policy_uses": {
            path: sum(
                event["event"] == "session_ready" and event["path"] == path
                for event in sender
            )
            for path in ("A", "B")
        },
        "wire_bytes": wire_bytes,
        "extra_path_bytes": extra_bytes,
        "statuses": {
            status: sum(event["status"] == status for event in complete)
            for status in sorted({event["status"] for event in complete})
        },
    }
    (output / "routing-summary.json").write_text(json.dumps(result, indent=2) + "\n")
    return result
