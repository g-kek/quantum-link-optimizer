"""Incident-level scoring of the diagnostics against laboratory truth.

A scenario declares which incidents should appear, where and from when.
Points in time are not scored separately, one incident counts once
"""

import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from .classes import describe

QUIET = {"telemetry_gap"}

# class, scope, onset, component that truly caused it
EXPECTED = {
    "normal": [],
    "receiver-restart": [],
    "swap": [
        ("path_degradation", "A", "start", "segment:A"),
        ("path_degradation", "B", "fault", "segment:B"),
    ],
    "common-700": [("path_degradation", "A+B", "start", None)],
    "shared-queue": [("path_degradation", "A+B", "start", "shared")],
    "loss-a": [("path_degradation", "A", "start", "segment:A")],
    "slow-kms": [("key_delivery_delay", "any", "start", "kms")],
    "key-exhaustion": [("key_exhaustion", "any", "pool_empty", "kms")],
}

# Symptoms that follow from the main fault and are not false alarms
CONSEQUENCES = {
    "common-700": {"sender_overload"},
    "shared-queue": {"sender_overload"},
    "slow-kms": {"sender_overload", "receiver_slow"},
    "key-exhaustion": {"sender_overload", "key_delivery_delay"},
    "swap": {"route_change"},
}


def read(path):
    if not path.exists():
        return []

    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def incident_class(event):
    return event.get("class") or describe(event["state"])["class"]


def onset_ns(kind, sender, truth, samples):
    if kind == "start":
        return min(e["time_ns"] for e in sender if e["event"] == "probe_result")

    if kind == "fault":
        return next(e["time_ns"] for e in truth if e["event"] == "fault")

    if kind == "pool_empty":
        return next(
            (
                e["time_ns"]
                for e in samples
                if e["event"] == "pool_truth"
                and any(value == 0 for value in e["available"].values())
            ),
            None,
        )

    raise ValueError(kind)


def slow_by_design(trial):
    """Paths whose configured RTT is already near the target are not faults."""
    config = json.loads((trial / "scenario.json").read_text())
    slow = set()

    for path, settings in config.get("network", {}).items():
        delay = settings["forward"].get("delay_ms", 0)
        delay += settings["reverse"].get("delay_ms", 0)

        if delay >= 0.9 * config.get("target_ms", 100):
            slow.add(path)

    return slow


def scope_matches(expected, scope):
    if expected == "any":
        return True

    return scope == expected


def score_trial(trial: Path, case: str):
    sender = read(trial / "sender.jsonl")
    truth = read(trial / "truth.jsonl")
    samples = read(trial / "samples.jsonl")
    opened = [e for e in sender if e["event"] == "incident_opened"]
    used = set()
    expected_rows = []

    for cls, scope, onset_kind, component in EXPECTED.get(case, []):
        onset = onset_ns(onset_kind, sender, truth, samples)
        match = None

        if onset is not None:
            for index, event in enumerate(opened):
                if (
                    index not in used
                    and incident_class(event) == cls
                    and scope_matches(scope, event["scope"])
                    and event["time_ns"] >= onset - 50e6
                ):
                    match = index
                    break

        if match is None and scope == "A+B":
            # Two separate path incidents still detect it, the scope is wrong
            parts = [
                i
                for i, e in enumerate(opened)
                if i not in used
                and incident_class(e) == cls
                and e["scope"] in {"A", "B"}
                and onset is not None
                and e["time_ns"] >= onset - 50e6
            ]

            if {opened[i]["scope"] for i in parts} == {"A", "B"}:
                match = parts[0]

        row = {
            "class": cls,
            "scope": scope,
            "onset": onset_kind,
            "detected": match is not None,
            "scope_correct": None,
            "time_to_detect_ms": None,
            "location_expected": component,
            "location_found": None,
            "location_correct": None,
        }

        if match is not None:
            used.add(match)
            event = opened[match]

            if scope == "any":
                # One fault seen on both paths is still one incident
                used.update(
                    i
                    for i, e in enumerate(opened)
                    if incident_class(e) == cls and e["time_ns"] >= onset - 50e6
                )

            location = event.get("location") or {}
            row.update(
                scope_correct=scope_matches(scope, event["scope"]),
                time_to_detect_ms=max(0.0, (event["time_ns"] - onset) / 1e6),
                location_found=location.get("component"),
            )

            if component is not None:
                row["location_correct"] = location.get("component") == component

        expected_rows.append(row)

    allowed = CONSEQUENCES.get(case, set())
    slow = slow_by_design(trial)
    extra = [
        e
        for i, e in enumerate(opened)
        if i not in used
        and incident_class(e) not in QUIET | allowed
        and not (incident_class(e) == "path_degradation" and e["scope"] in slow)
    ]
    return {
        "case": case,
        "trial": trial.name,
        "expected": expected_rows,
        "false_alarms": len(extra),
        "false_alarm_classes": dict(Counter(incident_class(e) for e in extra)),
        "telemetry_alarms": sum(incident_class(e) in QUIET for e in opened),
        "opened_classes": sorted({incident_class(e) for e in opened}),
    }


def summarize(rows):
    expected = [item for row in rows for item in row["expected"]]
    detected = [item for item in expected if item["detected"]]
    located = [item for item in detected if item["location_correct"] is not None]
    times = [item["time_to_detect_ms"] for item in detected]
    by_case = defaultdict(list)

    for row in rows:
        by_case[row["case"]].append(row)

    confusion = defaultdict(Counter)

    for row in rows:
        classes = row["opened_classes"]
        expected_classes = {item["class"] for item in row["expected"]} or {"none"}

        for cls in expected_classes:
            for found in classes or ["none"]:
                confusion[cls][found] += 1

    return {
        "trials": len(rows),
        "expected_incidents": len(expected),
        "detected": len(detected),
        "detection_rate": len(detected) / len(expected) if expected else None,
        "scope_correct": sum(bool(item["scope_correct"]) for item in detected),
        "location_checked": len(located),
        "location_correct": sum(item["location_correct"] for item in located),
        "time_to_detect_ms_median": float(np.median(times)) if times else None,
        "time_to_detect_ms_p90": float(np.quantile(times, 0.9)) if times else None,
        "false_alarms": sum(row["false_alarms"] for row in rows),
        "false_alarms_per_trial": sum(row["false_alarms"] for row in rows) / len(rows)
        if rows
        else None,
        "telemetry_alarms": sum(row["telemetry_alarms"] for row in rows),
        "by_case": {
            case: {
                "trials": len(items),
                "detected": sum(e["detected"] for r in items for e in r["expected"]),
                "expected": sum(len(r["expected"]) for r in items),
                "false_alarms": sum(r["false_alarms"] for r in items),
                "false_alarm_classes": dict(
                    sum((Counter(r["false_alarm_classes"]) for r in items), Counter())
                ),
            }
            for case, items in sorted(by_case.items())
        },
        "opened_by_expected_class": {k: dict(v) for k, v in confusion.items()},
    }
