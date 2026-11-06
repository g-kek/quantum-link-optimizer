import asyncio
import json
from pathlib import Path

import pytest

from quantum_link_optimizer.collection.build import build_dataset
from quantum_link_optimizer.collection.collect import collect_episode
from quantum_link_optimizer.collection.freeze import freeze, read_lock
from quantum_link_optimizer.collection.plan import episodes
from quantum_link_optimizer.collection.series import collect_series
from quantum_link_optimizer.telemetry_lab.manifest import verify_files
from quantum_link_optimizer.telemetry_lab.report import records

PLAN = Path(__file__).parents[2] / "lab" / "datasets" / "smoke.json"


@pytest.fixture
def plan():
    value = json.loads(PLAN.read_text())
    value["duration_s"] = 0.05
    value["profiles"] = ["recoverable-700"]
    value["base"]["probe_timeout_s"] = 1
    return value


def test_real_packets_to_dataset_and_frozen_policy(tmp_path, plan):
    series = tmp_path / "series"
    asyncio.run(collect_series(plan, series, ["train", "validation"]))
    dataset = tmp_path / "dataset"
    quality = build_dataset(series, dataset)
    assert quality["episodes"] == {"train": 2, "validation": 2}
    assert all(verify_files(dataset).values())
    training = records(dataset / "train.jsonl")
    assert len(training) == 4
    a = [row for row in training if row["path"] == "A"]
    b = [row for row in training if row["path"] == "B"]
    assert all(row["targets"]["rtt_ms"] >= 680 for row in a)
    # This tests faithful collection/labels, not the scheduling latency of CI.
    assert all(row["targets"]["rtt_ms"] >= 55 for row in b)
    for row in training:
        probes = records(series / row["episode_id"] / "sender.jsonl")
        measured = next(
            e
            for e in probes
            if e["event"] == "probe_result"
            and e["sample"] == row["sample"]
            and e["path"] == row["path"]
        )
        assert row["targets"]["rtt_ms"] == measured["rtt_ms"]
        assert row["targets"]["probe_within_target"] == (
            measured["rtt_ms"] <= plan["base"]["target_ms"]
        )
    assert all(row["features"]["last_rtt_ms"] is None for row in a + b)
    assert all(
        row["targets"]["command_status"] is None
        for row in training
        if row["path"]
        != json.loads((series / row["episode_id"] / "scenario.json").read_text())[
            "fixed_path"
        ]
    )
    policy = tmp_path / "policy.json"
    policy.write_text('{"name": "ewma", "alpha": 0.3}')
    lock = tmp_path / "freeze.json"
    freeze(dataset, policy, lock)
    assert read_lock(lock)["policy"]["path"] == str(policy.resolve())
    asyncio.run(collect_series(plan, series, ["train", "validation"], resume=True))
    policy.write_text('{"name": "changed"}')
    with pytest.raises(ValueError, match="changed"):
        read_lock(lock)


def test_final_collection_requires_freeze_before_creating_output(tmp_path, plan):
    with pytest.raises(ValueError, match="frozen"):
        asyncio.run(collect_series(plan, tmp_path / "final", ["final_test"]))
    assert not (tmp_path / "final").exists()


def test_integrity_and_incomplete_series_are_not_silently_used(tmp_path, plan):
    series = tmp_path / "series"
    series.mkdir()
    configs = [config for config in episodes(plan) if config["split"] != "final_test"]
    definition = {
        "episodes": configs,
        "splits": ["train", "validation"],
        "frozen_policy_sha256": None,
    }
    (series / "series.json").write_text(json.dumps({"definition": definition}))
    config = next(c for c in configs if c["split"] == "train")
    path = series / config["episode_id"]
    asyncio.run(collect_episode(config, path))
    with pytest.raises(ValueError, match="incomplete"):
        build_dataset(series, tmp_path / "strict")
    build_dataset(series, tmp_path / "partial", allow_partial=True)
    assert not json.loads((tmp_path / "partial" / "manifest.json").read_text())[
        "complete"
    ]
    (path / "sender.jsonl").write_text("tampered")
    with pytest.raises(ValueError, match="integrity"):
        build_dataset(series, tmp_path / "tampered", allow_partial=True)
