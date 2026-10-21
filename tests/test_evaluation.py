from itertools import product

import numpy as np
import pytest

from quantum_link_optimizer.evaluation import (
    evaluate_policies,
    hindsight_oracle,
    policy_metrics,
    select_channels,
)


def test_unconditional_metrics_and_switch_penalty():
    actual = np.array([[90.0, 120.0], [110.0, 99.0], [110.0, 95.0]])
    result = policy_metrics(actual, np.array([0, 1, 1]), 5.0)
    assert result["samples"] == 3
    assert result["mean_rtt_ms"] == pytest.approx((90 + 99 + 95) / 3)
    assert result["mean_effective_rtt_ms"] == pytest.approx((90 + 104 + 95) / 3)
    assert result["p95_rtt_ms"] == pytest.approx(98.6)
    assert result["sla_violation_rate"] == 0
    assert result["effective_sla_violation_rate"] == pytest.approx(1 / 3)
    assert result["switches"] == 1
    assert result["switch_rate"] == 0.5


def test_sla_boundary_is_inclusive():
    result = policy_metrics(np.array([[100.0, 110.0]]), np.array([0]), 3.0)
    assert result["sla_violation_rate"] == 0
    assert result["switches"] == 0


def test_switch_penalty_changes_causal_selector():
    forecast = np.array([[60.0, 70.0], [65.0, 64.0], [70.0, 60.0]])
    np.testing.assert_array_equal(select_channels(forecast, 3), [0, 0, 1])
    changed = forecast.copy()
    changed[2] = [1, 999]
    np.testing.assert_array_equal(
        select_channels(forecast, 3)[:2],
        select_channels(changed, 3)[:2],
    )


def test_oracle_matches_brute_force_minimum():
    actual = np.array([[60.0, 62.0], [70.0, 60.0], [59.0, 63.0], [66.0, 60.0]])
    cost = 4.0
    result = policy_metrics(actual, hindsight_oracle(actual, cost), cost)
    brute = min(
        policy_metrics(actual, np.array(path), cost)["mean_effective_rtt_ms"]
        for path in product(range(2), repeat=len(actual))
    )
    assert result["mean_effective_rtt_ms"] == brute
    policies, _ = evaluate_policies(actual, actual, actual, cost, 100)
    assert all(
        result["mean_effective_rtt_ms"] <= p["mean_effective_rtt_ms"]
        for p in policies.values()
    )


@pytest.mark.parametrize("cost", [-1.0, float("nan"), float("inf")])
def test_invalid_cost(cost):
    with pytest.raises(ValueError):
        evaluate_policies(np.ones((2, 2)), np.ones((2, 2)), np.ones((2, 2)), cost, 100)
