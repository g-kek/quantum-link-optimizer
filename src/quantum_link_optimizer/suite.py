"""Predeclared multiseed scenarios and exact saved-model shadow checks."""

import csv
import json
from pathlib import Path

import numpy as np

from quantum_link_optimizer.risk import run_risk
from quantum_link_optimizer.shadow import run_shadow
from quantum_link_optimizer.warning import dump_json


def run_suite(output: Path, seeds=(41, 42, 43), samples=4000, epochs=40):
    runs = []
    for scenario in ("normal", "shared-stress"):
        for seed in seeds:
            directory = output / f"{scenario}-{seed}"
            risk = run_risk(directory, samples, seed, epochs, scenario)
            replay = run_shadow(
                directory / "warning-model.pt",
                directory / "telemetry.csv",
                directory / "replay",
            )
            with (directory / "risk-decisions.csv").open(newline="") as stream:
                batch_choices = [
                    (int(row["sample"]), int(row["risk_selector"]))
                    for row in csv.DictReader(stream)
                ]
            with (directory / "replay" / "shadow.jsonl").open() as stream:
                streaming_choices = [
                    (row["sample"], row["decision"])
                    for line in stream
                    if (row := json.loads(line))["kind"] == "decision"
                ]
            if batch_choices != streaming_choices:
                raise ValueError(
                    "Streaming decisions differ from batch at sample level"
                )
            selected = risk["policies"]["risk_selector"]
            if not np.isclose(
                replay["mean_selected_rtt_ms_available_only"],
                selected["mean_rtt_ms"],
                rtol=0,
                atol=1e-10,
            ):
                raise ValueError(
                    "Saved-model streaming decisions differ from batch experiment"
                )
            if replay["switches"] != selected["switches"]:
                raise ValueError("Streaming switches differ from batch")
            runs.append(
                {"seed": seed, "scenario": scenario, "risk": risk, "replay": replay}
            )
    report = {
        "seeds": list(seeds),
        "samples": samples,
        "epochs": epochs,
        "scenarios": ["normal", "shared-stress"],
        "test_tuning": False,
        "runs": runs,
    }
    dump_json(output / "suite-report.json", report)
    return report
