import asyncio
import json
from pathlib import Path

import pytest

from quantum_link_optimizer.telemetry_lab.episode import run_episode
from quantum_link_optimizer.telemetry_lab.events import EventLog
from quantum_link_optimizer.telemetry_lab.manifest import verify_files
from quantum_link_optimizer.telemetry_lab.observations import available_to_sender
from quantum_link_optimizer.telemetry_lab.report import records

EPISODES = Path(__file__).parents[2] / "lab" / "episodes"


@pytest.mark.parametrize(
    "name", ["normal", "slow-network", "slow-kms", "empty-pool", "lost-ack", "rotation"]
)
def test_reproducible_episode_and_no_future_observations(tmp_path, name):
    output = tmp_path / name
    config = json.loads((EPISODES / f"{name}.json").read_text())
    asyncio.run(run_episode(config, output))
    assert all(verify_files(output).values())
    summary = json.loads((output / "summary.json").read_text())
    assert summary["submitted"] == config["count"]
    assert not summary["missing_completion"]
    events = records(output / "sender.jsonl")
    assert available_to_sender(output, 0) == []
    cutoff = events[len(events) // 2]["available_ns"]
    assert all(
        event["algorithm_available_ns"] <= cutoff
        for event in available_to_sender(output, cutoff)
    )
    assert all(event["occurred_ns"] <= event["available_ns"] for event in events)
    assert not available_to_sender(output, cutoff)[-1].get("network")
    if name == "empty-pool":
        assert summary["receiver_executions"] == 0
    elif name == "normal":
        assert summary["timely_executed"] == config["count"]
    if name == "slow-kms":
        assert min(summary["phases"]["startup"]["key_wait_ns"]) >= 70_000_000
    if name == "rotation":
        assert max(summary["phases"]["rotation"]["key_wait_ns"]) >= 50_000_000
    if name == "slow-network":
        assert (
            min(
                event["rtt_ns"]
                for event in events
                if event["event"] == "ack_received" and event["rtt_ns"] is not None
            )
            >= 70_000_000
        )
    (output / "sender.jsonl").write_text("tampered")
    assert not verify_files(output)["sender.jsonl"]


def test_secrets_and_remote_observations_are_excluded(tmp_path):
    journal = EventLog(tmp_path / "receiver.jsonl")
    try:
        with pytest.raises(ValueError, match="secret"):
            journal.emit("bad", material="do not log")
        journal.emit("received", command_id="test")
    finally:
        journal.close()
    assert records(tmp_path / "receiver.jsonl")[0]["algorithm_available_ns"] is None


def test_failed_episode_still_has_manifest(tmp_path, monkeypatch):
    from quantum_link_optimizer.telemetry_lab import episode

    async def fail(*args, **kwargs):
        raise OSError("test bind failure")

    monkeypatch.setattr(episode, "start_server", fail)
    config = json.loads((EPISODES / "normal.json").read_text())
    output = tmp_path / "failed"
    with pytest.raises(OSError):
        asyncio.run(run_episode(config, output))
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["status"] == "failed"
    assert all(verify_files(output).values())


def test_heldout_configs_are_separate():
    configs = {
        path.stem: json.loads(path.read_text()) for path in EPISODES.glob("*.json")
    }
    development = {
        config["seed"]
        for config in configs.values()
        if config["split"] == "development"
    }
    heldout = {
        config["seed"] for config in configs.values() if config["split"] == "final_test"
    }
    assert heldout and development.isdisjoint(heldout)


def test_manifest_recovers_wal_after_receiver_exit(tmp_path):
    import sqlite3
    import subprocess
    import sys

    from quantum_link_optimizer.telemetry_lab.manifest import begin, finish

    output = tmp_path / "crashed"
    manifest = begin(output, {"seed": 1})
    database = output / "receiver.sqlite"
    subprocess.run(
        [
            sys.executable,
            "-c",
            """
import os
import sqlite3
import sys
connection = sqlite3.connect(sys.argv[1])
connection.execute('PRAGMA journal_mode=WAL')
connection.execute('CREATE TABLE state (value INTEGER)')
connection.execute('INSERT INTO state VALUES (42)')
connection.commit()
os._exit(0)
""",
            str(database),
        ],
        check=True,
    )
    finish(output, manifest)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT value FROM state").fetchone() == (42,)
    assert all(verify_files(output).values())
