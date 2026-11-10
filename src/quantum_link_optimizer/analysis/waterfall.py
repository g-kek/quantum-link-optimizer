"""Per-command latency split for laboratory trials, from arrival to ACK."""

import json
from collections import defaultdict
from pathlib import Path

import numpy as np

STAGES = (
    ("source_lag", "Опоздание генератора"),
    ("router_queue", "Очередь и pacing"),
    ("key_wait", "Ожидание ключа"),
    ("send", "Шифрование и отправка"),
    ("network", "Сеть и повторы"),
    ("receiver_queue", "Очередь получателя"),
    ("receiver_key", "Ключ у получателя"),
    ("receiver_processing", "Исполнение"),
    ("ack", "Обработка ACK"),
)


def read_events(path: Path):
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def command_stages(events):
    by_command = defaultdict(list)

    for event in events:
        if event.get("command_id") is not None:
            by_command[event["command_id"]].append(event)

    commands = []

    for command_id, items in by_command.items():
        done = next(
            (e for e in items if e["event"] == "command_completed"),
            None,
        )

        if done is None or "elapsed_ns" not in done:
            continue

        path = done.get("path")
        stage = dict.fromkeys((name for name, _ in STAGES), 0.0)

        for event in items:
            if event["event"] == "workload_arrival":
                stage["source_lag"] = event["schedule_lag_ns"] / 1e6
            elif event["event"] == "route_decision":
                stage["router_queue"] = event["queue_wait_ns"] / 1e6
            elif event["event"] == "session_ready" and event.get("path") == path:
                stage["key_wait"] = event["key_wait_ns"] / 1e6
            elif event["event"] == "attempt_sent" and event.get("path") == path:
                stage["send"] += event["sender_send_processing_ns"] / 1e6

        for name, field in (
            ("receiver_queue", "receiver_queue_wait_ns"),
            ("receiver_key", "receiver_key_wait_ns"),
            ("receiver_processing", "receiver_processing_ns"),
            ("ack", "sender_ack_processing_ns"),
        ):
            stage[name] = (done.get(field) or 0) / 1e6

        elapsed = done["elapsed_ns"] / 1e6
        measured = sum(value for name, value in stage.items() if name != "source_lag")

        # Everything not covered by a span is the wire, relays and retries
        stage["network"] = max(elapsed - measured, 0.0)
        commands.append(
            {
                "command_id": command_id,
                "path": path,
                "status": done["status"],
                "total_ms": stage["source_lag"] + elapsed,
                "stages_ms": stage,
            }
        )

    return sorted(commands, key=lambda item: int(item["command_id"].split("-")[-1]))


def composition(commands, rows):
    if not rows:
        return None

    total = float(np.mean([row["total_ms"] for row in rows]))
    parts = []

    for name, label in STAGES:
        value = float(np.mean([row["stages_ms"][name] for row in rows]))
        parts.append(
            {
                "stage": name,
                "label": label,
                "mean_ms": value,
                "share": value / total if total else None,
            }
        )

    return {"commands": len(rows), "mean_total_ms": total, "stages": parts}


def breakdown(trial: Path, target_ms=100.0):
    commands = command_stages(read_events(trial / "sender.jsonl"))

    if not commands:
        return None

    totals = np.array([item["total_ms"] for item in commands])
    p95 = float(np.quantile(totals, 0.95))
    median = float(np.median(totals))
    tail = [item for item in commands if item["total_ms"] >= p95]
    typical = [item for item in commands if item["total_ms"] <= median]
    late = [item for item in commands if item["total_ms"] > target_ms]
    return {
        "trial": trial.name,
        "commands": commands,
        "median_ms": median,
        "p95_ms": p95,
        "typical": composition(commands, typical),
        "tail": composition(commands, tail),
        "late": composition(commands, late),
        "largest_tail_stage": max(
            composition(commands, tail)["stages"], key=lambda part: part["mean_ms"]
        )["label"],
        "note": (
            "Сеть и повторы считаются как остаток после измеренных отрезков, "
            "ожидание ключа и очередь при копиях могут перекрываться"
        ),
    }
