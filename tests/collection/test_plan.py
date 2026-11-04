import json
from pathlib import Path

import pytest

from quantum_link_optimizer.collection.plan import episodes

PLAN = Path(__file__).parents[2] / "lab" / "datasets" / "high-rtt.json"


def test_long_plan_is_grouped_and_held_out():
    plan = json.loads(PLAN.read_text())
    configs = episodes(plan)
    assert len(configs) == len({config["episode_id"] for config in configs}) == 144
    for split in ("train", "validation", "final_test"):
        selected = [config for config in configs if config["split"] == split]
        others = [config for config in configs if config["split"] != split]
        assert {config["seed"] for config in selected}.isdisjoint(
            config["seed"] for config in others
        )
        assert all(
            config["samples"] * config["interval_s"] >= 600 for config in selected
        )
    positive = next(c for c in configs if c["profile"] == "recoverable-700")
    negative = next(c for c in configs if c["profile"] == "common-700")
    assert sum(v["delay_ms"] for v in positive["network"]["A"].values()) == 700
    assert sum(v["delay_ms"] for v in positive["network"]["B"].values()) == 60
    assert all(
        sum(v["delay_ms"] for v in link.values()) == 700
        for link in negative["network"].values()
    )


def test_overlapping_seeds_are_rejected():
    plan = json.loads(PLAN.read_text())
    plan["splits"]["final_test"]["seeds"] = plan["splits"]["train"]["seeds"]
    with pytest.raises(ValueError, match="unique"):
        episodes(plan)
