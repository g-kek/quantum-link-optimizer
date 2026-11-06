import json

from quantum_link_optimizer.collection import build
from quantum_link_optimizer.collection.freeze import freeze
from quantum_link_optimizer.telemetry_lab.events import EventLog
from quantum_link_optimizer.telemetry_lab.manifest import begin, finish, verify_files


def synthetic_series(path, split, seed, lock=None):
    path.mkdir()
    config = {
        "episode_id": f"unit-{split}",
        "split": split,
        "seed": seed,
        "samples": 1,
        "max_age_ms": 2000,
        "target_ms": 100,
        "profile": "unit-fixture",
    }
    source = path / config["episode_id"]
    manifest = begin(source, config)
    journal = EventLog(source / "sender.jsonl")
    journal.emit("decision", sample=0)
    for candidate in ("A", "B"):
        journal.emit("probe_result", sample=0, path=candidate, rtt_ms=60, status="ok")
    journal.close()
    manifest["status"] = "collected"
    finish(source, manifest)
    definition = {
        "episodes": [config],
        "splits": [split],
        "frozen_policy_sha256": None,
        "frozen": lock,
    }
    (path / "series.json").write_text(json.dumps({"definition": definition}))


def test_final_build_does_not_fit_and_uses_frozen_training_stats(tmp_path, monkeypatch):
    development = tmp_path / "development"
    synthetic_series(development, "train", 1)
    dataset = tmp_path / "dataset"
    build.build_dataset(development, dataset)
    policy = tmp_path / "policy.json"
    policy.write_text('{"name": "unit-fixture"}')
    lock_path = tmp_path / "freeze.json"
    lock = freeze(dataset, policy, lock_path)
    final_series = tmp_path / "final-series"
    synthetic_series(final_series, "final_test", 1001, lock)

    def forbidden(*args):
        raise AssertionError("fitting final_test would leak labels")

    monkeypatch.setattr(build, "fit", forbidden)
    final_dataset = tmp_path / "final-dataset"
    stats = dataset / "preprocessing.json"
    build.build_dataset(final_series, final_dataset, frozen_preprocessing=stats)
    assert (final_dataset / "preprocessing.json").read_bytes() == stats.read_bytes()
    assert all(verify_files(final_dataset).values())
    assert not (final_dataset / "train.jsonl").exists()
