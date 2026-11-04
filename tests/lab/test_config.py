import json
from pathlib import Path

import pytest

from quantum_link_optimizer.lab.config import load_config

SCENARIOS = Path(__file__).parents[2] / "lab" / "scenarios"


@pytest.mark.parametrize("path", sorted(SCENARIOS.glob("*.json")))
def test_scenario_is_valid(path):
    assert load_config(path)["name"] == path.stem


@pytest.mark.parametrize(
    "settings",
    [
        {"loss_pct": 101},
        {"delay_ms": -1},
        {"rate_mbit": 0},
        {"reorder_pct": 10},
        {"limit": 1.5},
        {"delay_ms": float("nan")},
        {"delay_ms": "10ms"},
        {"delai_ms": 10},
    ],
)
def test_rejects_invalid_link_before_network_changes(tmp_path, settings):
    config = json.loads((SCENARIOS / "baseline.json").read_text())
    config["paths"]["A"]["forward"] = settings
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(config))
    with pytest.raises(ValueError):
        load_config(path)
