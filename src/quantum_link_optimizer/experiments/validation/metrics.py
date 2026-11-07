import json
import random
from collections import Counter, defaultdict

from quantum_link_optimizer.experiments.common import distribution, quantile
from quantum_link_optimizer.telemetry_lab.report import records


def trial_metrics(path, summary, case):
    sender = records(path / "sender.jsonl")
    receiver = records(path / "receiver.jsonl")
    routes = [e for e in sender if e["event"] == "route_decision"]
    completed = [e for e in sender if e["event"] == "command_completed"]
    attempts = [e for e in sender if e["event"] == "attempt_sent"]
    key_events = [e for e in sender if e["event"] == "session_ready"]
    handling = defaultdict(int)

    processing_field = {
        "attempt_sent": "sender_send_processing_ns",
        "ack_received": "sender_ack_processing_ns",
        "command_result": "receiver_processing_ns",
        "ack_sent": "receiver_ack_processing_ns",
    }

    for event in sender + receiver:
        command = event.get("command_id")
        field = processing_field.get(event["event"])

        if command is not None and field is not None:
            handling[command] += event.get(field) or 0

    arrivals = {
        e["command_id"]: e["available_ns"] - e["schedule_lag_ns"]
        for e in sender
        if e["event"] == "workload_arrival"
    }
    source_elapsed = {
        e["command_id"]: (e["available_ns"] - arrivals[e["command_id"]]) / 1e6
        for e in completed
        if e["command_id"] in arrivals
    }
    target_ms = json.loads((path / "scenario.json").read_text())["target_ms"]
    source_timely = sum(
        e["status"] == "executed"
        and source_elapsed.get(e["command_id"], float("inf")) <= target_ms
        for e in completed
    )

    pool_history = records(path / "samples.jsonl")
    statuses = Counter()
    kms_positive = 0
    false_kms = 0
    absent_kms = 0

    for decision in routes:
        findings = decision["diagnostics"]
        statuses.update(f["status"] for f in findings)
        signalled = any("observed_key_wait" in f.get("evidence", []) for f in findings)
        inventory = next(
            (
                e
                for e in reversed(pool_history)
                if e["time_ns"] <= decision["available_ns"]
            ),
            None,
        )
        depleted = inventory is not None and any(
            v == 0 for v in inventory["available"].values()
        )
        expected = case == "slow-kms" or case == "key-exhaustion" and depleted
        kms_positive += int(signalled and expected)
        false_kms += int(signalled and not expected)
        absent_kms += int(expected and not signalled)

    return {
        **summary,
        "timely_executed": source_timely,
        "timely_fraction": source_timely / summary["offered"]
        if summary["offered"]
        else None,
        "source_latency_ms": distribution(source_elapsed.values()),
        "source_clock": (
            "Scheduled open-loop arrivals; generator scheduling lag is "
            "included in timely outcomes"
        ),
        "end_to_end_ms": distribution(
            e["elapsed_ns"] / 1e6 for e in completed if "elapsed_ns" in e
        ),
        "successful_ack_ms": distribution(
            e["elapsed_ns"] / 1e6
            for e in completed
            if e["status"] == "executed" and "elapsed_ns" in e
        ),
        "decision_us": distribution(e["decision_elapsed_ns"] / 1000 for e in routes),
        "queue_and_pacing_ms": distribution(e["queue_wait_ns"] / 1e6 for e in routes),
        "key_wait_ms": distribution(e["key_wait_ns"] / 1e6 for e in key_events),
        "observed_handling_lower_bound_ms": distribution(
            value / 1e6 for value in handling.values()
        ),
        "schedule_lag_ms": distribution(
            e["schedule_lag_ns"] / 1e6
            for e in sender
            if e["event"] == "workload_arrival"
        ),
        "actual_udp_payload_bytes": distribution(
            e["wire_bytes"] - 28 for e in attempts
        ),
        "unknown_outcomes": sum(e.get("outcome") == "unknown" for e in completed),
        "missing_latency_measurements": sum("elapsed_ns" not in e for e in completed),
        "diagnosis": {
            "status_counts": dict(statuses),
            "kms_signal_true_positive_decisions": kms_positive,
            "kms_signal_false_positive_decisions": false_kms,
            "kms_signal_false_negative_decisions": absent_kms,
            "scope": (
                "Declared scenario-family signal check; not exact fault "
                "localisation or attack attribution"
            ),
        },
        "resource_usage": json.loads((path / "resource-usage.json").read_text()),
        "phase_note": (
            "RTT includes receiver work; key/queue spans can overlap under "
            "copies. Do not sum these marginal distributions."
        ),
        "handling_note": (
            "Instrumented crypto/store spans are a lower bound; excludes some "
            "parsing/dispatch/key-cache work. Not a proof of the complete 1 ms "
            "agent budget."
        ),
    }


def paired_bootstrap(rows, candidate="forecast", baseline="ewma", seed=42):
    pairs = defaultdict(dict)

    for row in rows:
        if row["policy"] in {candidate, baseline}:
            pairs[(row["case"], row["seed"])][row["policy"]] = row["timely_fraction"]

    differences = [p[candidate] - p[baseline] for p in pairs.values() if len(p) == 2]

    if not differences:
        return {"paired_episodes": 0, "mean_difference": None, "interval_95": None}

    rng = random.Random(seed)
    means = [
        sum(rng.choices(differences, k=len(differences))) / len(differences)
        for _ in range(2000)
    ]

    return {
        "paired_episodes": len(differences),
        "mean_difference": sum(differences) / len(differences),
        "interval_95": [quantile(means, 0.025), quantile(means, 0.975)],
        "scope": (
            "Descriptive episode bootstrap; repeated scenario families are not "
            "a field-network population"
        ),
    }
