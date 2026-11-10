"""Build a portable offline report from verified experiment artifacts."""

import argparse
import json
from importlib.resources import files
from pathlib import Path

from quantum_link_optimizer.analysis.waterfall import (
    STAGES,
    command_stages,
    composition,
)
from quantum_link_optimizer.diagnostics.classes import describe
from quantum_link_optimizer.experiments.common import write_json
from quantum_link_optimizer.experiments.validation.freeze import read_lock
from quantum_link_optimizer.telemetry_lab.manifest import digest, verify_files
from quantum_link_optimizer.telemetry_lab.report import records

# Updates only refresh an open incident, the report keeps lifecycle changes
INCIDENT_EVENTS = {
    "incident_opened": "open",
    "incident_unobserved": "unobserved",
    "incident_resolved": "resolved",
}


def incident(event, time_s):
    location = event.get("location") or {}

    # Journals written before classes existed get them from the state name
    known = describe(event["state"])
    return {
        "t": time_s,
        "lifecycle": INCIDENT_EVENTS[event["event"]],
        "id": event["incident_id"],
        "state": event["state"],
        "class": event.get("class", known["class"]),
        "title": event.get("title", known["title"]),
        "domain": event.get("domain", known["domain"]),
        "action": event.get("action", known["action"]),
        "scope": event["scope"],
        "status": event.get("status"),
        "measurements": [
            {
                key: item.get(key)
                for key in ("metric", "value", "unit", "age_ms", "source")
            }
            for item in event.get("measurements", [])
        ],
        "alternatives": event.get("alternatives", []),
        "location": {
            "title": location.get("title"),
            "component": location.get("component"),
            "ambiguous": location.get("ambiguous"),
            "candidates": [
                item["component"] for item in location.get("candidates", [])
            ],
        }
        if location
        else None,
    }


def stages(sender):
    commands = command_stages(sender)

    if not commands:
        return None

    totals = sorted(item["total_ms"] for item in commands)
    p95 = totals[int(0.95 * (len(totals) - 1))]
    return {
        "labels": dict(STAGES),
        "commands": [
            {
                "id": item["command_id"],
                "status": item["status"],
                "total_ms": item["total_ms"],
                "stages_ms": item["stages_ms"],
            }
            for item in commands
        ],
        "tail": composition(commands, [i for i in commands if i["total_ms"] >= p95]),
    }


def verified(path):
    manifest = json.loads((path / "manifest.json").read_text())
    checks = verify_files(path)

    if manifest["status"] != "collected" or not checks or not all(checks.values()):
        raise ValueError("incomplete or corrupted experiment evidence")

    return manifest


def trial_path(validation, row):
    path = (validation / row["relative_path"]).resolve()

    if not path.is_relative_to(validation.resolve()):
        raise ValueError("trial path escapes the validation directory")

    return path


def trace(validation, row):
    path = trial_path(validation, row)
    manifest = verified(path)

    if digest(path / "manifest.json") != row["manifest_sha256"]:
        raise ValueError("trial manifest differs from the frozen report")

    anchor = manifest["clock_anchor"]["monotonic_ns"]
    sender = records(path / "sender.jsonl")
    arrivals = {
        e["command_id"]: e["available_ns"] - e["schedule_lag_ns"]
        for e in sender
        if e["event"] == "workload_arrival"
    }
    events = []

    for event in sender:
        time_s = (event["available_ns"] - anchor) / 1e9
        kind = event["event"]

        if kind in INCIDENT_EVENTS:
            events.append({"kind": "incident", **incident(event, time_s)})

        if kind == "probe_result":
            events.append(
                {
                    "kind": "probe",
                    "t": time_s,
                    "path": event["path"],
                    "rtt_ms": event.get("rtt_ms"),
                    "status": event["status"],
                }
            )

        if kind == "kms_observed":
            events.append(
                {
                    "kind": "keys",
                    "t": time_s,
                    "available": event["available"],
                    "pending": event["pending"],
                }
            )

        if kind == "session_ready":
            events.append(
                {
                    "kind": "key_wait",
                    "t": time_s,
                    "path": event["path"],
                    "command_id": event["command_id"],
                    "sender_key_wait_ms": event["key_wait_ns"] / 1e6,
                }
            )

        if kind == "route_decision":
            observations = {
                path: {
                    name: values.get(name)
                    for name in (
                        "ewma_rtt_ms",
                        "observation_age_ms",
                        "predicted_late_probability",
                        "kms_available_keys",
                        "kms_observation_age_ms",
                    )
                }
                for path, values in event["observations"].items()
            }
            events.append(
                {
                    "kind": "decision",
                    "t": time_s,
                    "path": event["path"],
                    "reason": event["reason"],
                    "observations": observations,
                    # Full findings are in the incident cards, here only the summary
                    "diagnostics": [
                        {key: item.get(key) for key in ("state", "scope", "status")}
                        for item in event["diagnostics"]
                    ],
                    "local_queue_depth": event["local_queue_depth"],
                    "queue_and_pacing_ms": event["queue_wait_ns"] / 1e6,
                }
            )

        if kind == "command_completed":
            started = arrivals[event["command_id"]]
            elapsed_ms = (event["available_ns"] - started) / 1e6
            events.append(
                {
                    "kind": "command",
                    "t": time_s,
                    "id": event["command_id"],
                    "path": event.get("path"),
                    "status": event["status"],
                    "latency_ms": elapsed_ms,
                    "receiver_key_wait_ms": (
                        event["receiver_key_wait_ns"] / 1e6
                        if event.get("receiver_key_wait_ns") is not None
                        else None
                    ),
                    "within_target": event["status"] == "executed"
                    and elapsed_ms <= 100,
                }
            )

    truth = [
        {**e, "t": (e["available_ns"] - anchor) / 1e9}
        for e in records(path / "truth.jsonl")
    ]
    config = json.loads((path / "scenario.json").read_text())
    truth.insert(
        0,
        {
            "t": 0,
            "event": "declared_initial_lab_conditions",
            "network": config["network"],
            "kms": config["kms"],
        },
    )

    return {
        **{
            k: row[k]
            for k in (
                "case",
                "seed",
                "policy",
                "offered",
                "timely_executed",
                "unknown_outcomes",
                "extra_path_bytes",
                "decision_us",
            )
        },
        "duration_s": max((e["t"] for e in events), default=0),
        "events": events,
        "truth": truth,
        "stages": stages(sender),
    }


def report_data(development, validation, diagnostics=None):
    development_manifest = verified(development)
    validation_manifest = verified(validation)
    lock = read_lock(validation / "policy-lock.json")
    forecast = json.loads((development / "forecasting-report.json").read_text())
    final = json.loads((validation / "validation-report.json").read_text())

    if final["model_sha256"] != forecast["model_sha256"]:
        raise ValueError("development/final models differ")

    if final["development_manifest_sha256"] != digest(development / "manifest.json"):
        raise ValueError("final report references different development data")

    if final["frozen_policy_sha256"] != digest(validation / "policy-lock.json"):
        raise ValueError("final policy lock differs")

    source_hash = validation_manifest["files"]["source.zip"]["sha256"]
    traces = []

    for row in final["trials"]:
        directory = trial_path(validation, row)
        manifest = verified(directory)

        if manifest["files"]["source.zip"]["sha256"] != source_hash:
            raise ValueError(
                "trial code/config snapshot changed during final collection"
            )

        traces.append(trace(validation, row))

    if not traces:
        raise ValueError("the demonstration needs real completed trials")

    accuracy = None

    if diagnostics is not None:
        evaluated = json.loads(diagnostics.read_text())
        accuracy = {
            "seeds": evaluated["plan"]["seeds"],
            **{
                key: evaluated["summary"][key]
                for key in (
                    "trials",
                    "expected_incidents",
                    "detected",
                    "location_checked",
                    "location_correct",
                    "false_alarms_per_trial",
                    "time_to_detect_ms_median",
                )
            },
            "baseline_false_alarms_per_trial": evaluated.get("baseline", {})
            .get("summary", {})
            .get("false_alarms_per_trial"),
        }

    return {
        "schema_version": 1,
        "diagnostics_accuracy": accuracy,
        "recommend_forecast": final["recommend_forecast"],
        "paired": final["paired_forecast_vs_ewma"],
        "trials": traces,
        "performance": final["performance"],
        "sources": {
            "origin": "Лабораторный UDP/TCP-обмен на одном хосте",
            "development_manifest_sha256": digest(development / "manifest.json"),
            "validation_manifest_sha256": digest(validation / "manifest.json"),
            "model_sha256": final["model_sha256"],
            "policy_lock_sha256": final["frozen_policy_sha256"],
            "final_code_snapshot_sha256": source_hash,
            "all_final_code_snapshots_identical": True,
            "frozen_utc": lock["frozen_utc"],
            "runtime": {
                name: validation_manifest.get(name)
                for name in ("platform", "python", "commit", "dirty")
            },
            "development_runtime": {
                name: development_manifest.get(name)
                for name in ("platform", "python", "commit", "dirty")
            },
        },
    }


def render_html(data):
    assets = files("quantum_link_optimizer").joinpath("templates/experiments")
    template = assets.joinpath("operator-demo.html").read_text()
    serialized = json.dumps(data, ensure_ascii=False, allow_nan=False).replace(
        "<", "\\u003c"
    )

    return (
        template.replace("__STYLE__", assets.joinpath("operator-demo.css").read_text())
        .replace("__SCRIPT__", assets.joinpath("operator-demo.js").read_text())
        .replace("__DATA__", serialized)
    )


def build(development, validation, output, diagnostics=None):
    data = report_data(development, validation, diagnostics)
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "demo-data.json", data)
    (output / "index.html").write_text(render_html(data))

    return output / "index.html"


def main():
    parser = argparse.ArgumentParser(
        description="Verified offline command/forecast/diagnostic demonstration"
    )
    parser.add_argument("--development", type=Path, required=True)
    parser.add_argument("--validation", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--diagnostics", type=Path, help="diagnostics-report.json with incident scores"
    )
    args = parser.parse_args()
    print(build(args.development, args.validation, args.output, args.diagnostics))


if __name__ == "__main__":
    main()
