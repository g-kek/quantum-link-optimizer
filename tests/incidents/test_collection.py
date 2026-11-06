import asyncio
import json
from pathlib import Path

from quantum_link_optimizer.collection.build import build_dataset
from quantum_link_optimizer.collection.collect import collect_episode
from quantum_link_optimizer.collection.features import rows
from quantum_link_optimizer.collection.plan import episodes
from quantum_link_optimizer.telemetry_lab.manifest import verify_files
from quantum_link_optimizer.telemetry_lab.report import records

PLAN = Path(__file__).parents[2] / "lab" / "datasets" / "equipment-clocks-smoke.json"


def config_for(name):
    plan = json.loads(PLAN.read_text())
    plan["profiles"] = [name]
    plan["duration_s"] = 0.2
    config = next(config for config in episodes(plan) if config["split"] == "train")
    config.update(samples=8, interval_s=0.025, command_every=8)
    config["network"] = {
        path: {"forward": {"delay_ms": 1}, "reverse": {"delay_ms": 1}}
        for path in ("A", "B")
    }
    config["incidents"]["monitor"] = {"every_samples": 1, "delay_s": 0.03}
    return config


def test_bc_fault_preserves_rtt_and_exports_separate_clock_targets(tmp_path):
    config = config_for("bc-step-a")
    config["incidents"]["events"] = [
        {"sample": 0, "clocks": {"A": {"offset_ns": 50000}}}
    ]
    series = tmp_path / "series"
    series.mkdir()
    output = series / config["episode_id"]
    asyncio.run(collect_episode(config, output))
    definition = {
        "episodes": [config],
        "splits": ["train"],
        "frozen_policy_sha256": None,
    }
    (series / "series.json").write_text(json.dumps({"definition": definition}))
    dataset = tmp_path / "dataset"
    quality = build_dataset(series, dataset)
    assert quality["rows"]["train"] == 16
    data = records(dataset / "train.jsonl")
    a = [row for row in data if row["path"] == "A"]
    assert all(row["targets"]["rtt_ms"] < 100 for row in a)
    assert all(row["targets"]["bc_within_budget_at_decision"] is False for row in a)
    assert a[0]["features"]["bc_offset_estimate_ns"] is None
    assert any(row["features"]["bc_offset_estimate_ns"] == 50000 for row in a)
    assert all(verify_files(dataset).values())
    manifest = json.loads((dataset / "manifest.json").read_text())
    assert manifest["feature_schema_version"] == 2
    assert manifest["sources"][0]["assumptions"][0]["status"] == "assumption"


def test_device_fault_affects_real_probe_packets(tmp_path):
    config = config_for("equipment-outage-a")
    config["incidents"]["events"] = [
        {"sample": 0, "devices": {"A": {"available": False}}}
    ]
    config["probe_timeout_s"] = 0.1
    config["command_deadline_s"] = 0.1
    output = tmp_path / "outage"
    asyncio.run(collect_episode(config, output))
    data = list(
        rows(config, records(output / "sender.jsonl"), records(output / "truth.jsonl"))
    )
    assert all(
        row["targets"]["probe_status"] == "timeout"
        for row in data
        if row["path"] == "A"
    )
    assert all(
        row["targets"]["probe_status"] == "ok" for row in data if row["path"] == "B"
    )
