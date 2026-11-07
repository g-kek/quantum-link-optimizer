"""One-command synthetic demonstration, with saved-model replay and HTML output."""

import csv
import json
from pathlib import Path

from quantum_link_optimizer.artifacts import dump_json, runtime_metadata
from quantum_link_optimizer.audit import feasibility, path_summaries
from quantum_link_optimizer.report import render_report
from quantum_link_optimizer.risk import run_risk
from quantum_link_optimizer.shadow import run_shadow
from quantum_link_optimizer.telemetry import read_telemetry


def run_demo(output: Path, samples=4000, seed=42, epochs=40, scenario="high-rtt"):
    run = output / "experiment"
    risk = run_risk(run, samples, seed, epochs, scenario)
    replay = run_shadow(run / "warning-model.pt", run / "telemetry.csv", run / "replay")
    with (run / "replay/shadow.jsonl").open() as stream:
        decisions = {
            row["sample"]: row
            for line in stream
            if (row := json.loads(line))["kind"] == "decision"
        }
    values = read_telemetry(run / "telemetry.csv")
    with (run / "risk-decisions.csv").open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    points = []
    for row in rows:
        t = int(row["sample"])
        if int(row["risk_selector"]) != decisions[t]["decision"]:
            raise ValueError("Demo replay differs from saved batch decisions")
        points.append(
            {
                "sample": t,
                "timestamp": f"Синтетическое время: {t} с",
                "information_cutoff": f"Синтетическое время: {t - 1} с",
                "rtt": values[t].tolist(),
                "status": ["ok", "ok"],
                "choices": {name: int(row[name]) for name in risk["policies"]},
                "reasons": {
                    name: decisions[t]["reason"]
                    if name == "risk_selector"
                    else (
                        "evaluation_only_future"
                        if "oracle" in name
                        else "causal_policy"
                    )
                    for name in risk["policies"]
                },
                "forecast_rtt_ms": decisions[t]["forecast_rtt_ms"],
            }
        )
    report = {
        "kind": "synthetic_demo",
        "title": "Выбор пути",
        "source_label": "Синтетический сценарий · не измерения МКС РЖД",
        "runtime": runtime_metadata(),
        "samples_total": samples,
        "scenario": scenario,
        "seed": seed,
        "sla_ms": 100.0,
        "configuration": risk["configuration"],
        "input_sha256": replay["input_sha256"],
        "model_sha256": replay["model_sha256"],
        "evaluation_note": (
            "Chronological test only. Synthetic and uncalibrated. "
            "High-rtt adds 625 ms to both generated paths, not a model of RZD. "
            "Constrained baselines share dwell/hysteresis/switching cost. "
            "Pointwise oracle sees future outcomes and is evaluation-only."
        ),
        "feasibility": feasibility(points, 100.0),
        "paths": path_summaries(points, 100.0),
        "policies": {
            name: {
                **metrics,
                "selected_outcome_coverage": 1.0,
                "abstentions": 0,
                "selected_timeouts": 0,
                "selected_missing": 0,
            }
            for name, metrics in risk["policies"].items()
        },
        "replay_verified": True,
        "points": points,
    }
    dump_json(output / "report.json", report)
    render_report(report, output)
    return report
