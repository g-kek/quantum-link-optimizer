import pytest

from quantum_link_optimizer.experiments.common import write_json
from quantum_link_optimizer.experiments.validation.freeze import create_lock, read_lock
from quantum_link_optimizer.experiments.validation.metrics import paired_bootstrap
from quantum_link_optimizer.telemetry_lab.manifest import begin, finish


def development(path):
    manifest = begin(
        path, {"train_seeds": [11], "validation_seeds": [101], "final_seeds": [1009]}
    )
    write_json(path / "model.json", {"unit_fixture": True})
    write_json(path / "forecasting-report.json", {"unit_fixture": True})
    manifest["status"] = "collected"
    finish(path, manifest)


def test_lock_rejects_overlap_and_later_model_changes(tmp_path):
    source = tmp_path / "development"
    development(source)

    with pytest.raises(ValueError, match="overlap"):
        create_lock(source, {"final_seeds": [11]}, tmp_path / "bad.json")

    lock = tmp_path / "lock.json"
    create_lock(source, {"final_seeds": [1009]}, lock)
    assert read_lock(lock)["development_seeds"] == [11, 101]
    write_json(source / "model.json", {"changed": True})

    with pytest.raises(ValueError, match="changed"):
        read_lock(lock)


def test_bootstrap_pairs_whole_episodes_and_does_not_invent_a_gain():
    rows = [
        {"case": "unit", "seed": seed, "policy": policy, "timely_fraction": 0.5}
        for seed in (1009, 1013)
        for policy in ("ewma", "forecast")
    ]
    result = paired_bootstrap(rows)
    assert result["paired_episodes"] == 2
    assert result["interval_95"] == [0, 0]
