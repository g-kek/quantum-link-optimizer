import json

from quantum_link_optimizer.telemetry_lab.report import records

from .features import vector


def labelled_rows(directory, episode_id):
    config = json.loads((directory / "scenario.json").read_text())
    decisions = json.loads((directory / "decision-features.json").read_text())
    completed = {
        e["command_id"]: e
        for e in records(directory / "sender.jsonl")
        if e["event"] == "command_completed"
    }
    inventory = records(directory / "samples.jsonl")
    horizon_ns = int(config["key_horizon_s"] * 1e9)
    rows = []

    for decision in decisions:
        outcome = completed.get(decision["command_id"])
        now = decision["decision_ns"]
        available = decision["features"].get("feature_available_ns")

        context_available = decision["features"].get("context_available_ns")

        if context_available is not None and context_available > now:
            raise ValueError("future KMS observation")

        if available is not None and available > now:
            raise ValueError("future feature observation")

        future = next((e for e in inventory if e["time_ns"] >= now + horizon_ns), None)
        pool = "shared" if config["kms"]["mode"] == "shared" else decision["path"]
        key_empty = int(future["available"][pool] == 0) if future else None
        late = None

        # The unchosen branch has no counterfactual command label.
        if outcome is not None and outcome.get("path") == decision["path"]:
            late = int(
                outcome["status"] != "executed"
                or outcome["elapsed_ns"] > config["target_ms"] * 1e6
            )

        rows.append(
            {
                "episode_id": episode_id,
                "decision_ns": now,
                "command_id": decision["command_id"],
                "path": decision["path"],
                "features": vector(decision["features"]),
                "targets": [late, key_empty],
            }
        )

    return rows
