import asyncio
import json

import pytest

from quantum_link_optimizer.experiments.forecasting.dataset import labelled_rows
from quantum_link_optimizer.experiments.runtime import run_trial
from quantum_link_optimizer.experiments.scenarios import configuration
from quantum_link_optimizer.selection.config import Settings
from quantum_link_optimizer.telemetry_lab.manifest import verify_files


def test_trial_preserves_unobserved_counterfactual_and_available_history(tmp_path):
    config = configuration("normal", 11, count=3, interval_s=0.04)
    output = tmp_path / "trial"
    summary = asyncio.run(run_trial(config, output, Settings(mode="fixed-a")))
    assert summary["offered"] == summary["completed"] == 3
    assert summary["duplicate_executions"] == 0
    assert all(verify_files(output).values())
    rows = labelled_rows(output, "train-1")
    assert len(rows) == 6
    assert all(r["targets"][0] is None for r in rows if r["path"] == "B")
    assert all(r["targets"][0] is not None for r in rows if r["path"] == "A")
    assert all(r["targets"][1] == 0 for r in rows)


def test_truth_changes_labels_not_features_and_future_input_is_rejected(tmp_path):
    config = configuration("normal", 19, count=2, interval_s=0.04)
    output = tmp_path / "trial"
    asyncio.run(run_trial(config, output, Settings(mode="fixed-a")))
    before = labelled_rows(output, "train-1")
    samples_path = output / "samples.jsonl"
    samples = [json.loads(line) for line in samples_path.read_text().splitlines()]

    for sample in samples:
        sample["available"]["shared"] = 0

    samples_path.write_text("".join(json.dumps(e) + "\n" for e in samples))
    after = labelled_rows(output, "train-1")
    assert [r["features"] for r in after] == [r["features"] for r in before]
    assert all(r["targets"][1] == 1 for r in after)

    features_path = output / "decision-features.json"
    features = json.loads(features_path.read_text())
    features[0]["features"]["context_available_ns"] = features[0]["decision_ns"] + 1
    features_path.write_text(json.dumps(features))

    with pytest.raises(ValueError, match="future KMS"):
        labelled_rows(output, "train-1")
