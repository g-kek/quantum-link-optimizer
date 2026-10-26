import numpy as np
import pytest

from quantum_link_optimizer.risk import (
    RiskConfig,
    RiskSelector,
    causal_ewma,
    point_choices,
    risk_choices,
    run_risk,
)


def test_hold_hysteresis_and_cost():
    selector = RiskSelector(RiskConfig(100, 3, 2, 3))
    assert selector.step(0, [0, 0], [50, 60]) == (0, "initial_minimum_objective")
    for t in (1, 2):
        assert selector.step(t, [1, 0], [50, 50]) == (0, "minimum_hold")
    assert selector.step(3, [0, 0], [56, 51]) == (0, "hysteresis")
    assert selector.step(4, [0, 0], [56.1, 51]) == (1, "objective_improvement")
    assert selector.step(5, [0, 1], [50, 50]) == (1, "minimum_hold")


def test_probability_affects_selection_and_causality():
    indices = np.arange(20)
    probability = np.tile([0.8, 0.1], (20, 1))
    forecast = np.tile([50, 60], (20, 1))
    before = risk_choices(indices, probability, forecast)
    assert np.all(before == 1)
    probability[10:] = [0, 1]
    forecast[10:] = [10, 200]
    after = risk_choices(indices, probability, forecast)
    np.testing.assert_array_equal(before[:10], after[:10])
    assert np.all(after[10:] == 0)
    no_risk = risk_choices(indices, probability, forecast, RiskConfig(0))
    assert no_risk[0] == 0


def test_invalid_risk_configuration():
    for args in ((-1,), (100, float("nan")), (100, 3, 2, 0)):
        with pytest.raises(ValueError):
            RiskConfig(*args)
    assert RiskSelector().step(0, [float("nan"), 0], [50, 60])[0] is None


def test_risk_artifacts_and_full_test(tmp_path):
    report = run_risk(tmp_path, samples=500, epochs=2)
    assert report["policies"]["risk_selector"]["samples"] == 100
    assert (tmp_path / "risk-decisions.csv").is_file()
    bound = report["both_paths_bad_rate_lower_bound"]
    for policy in report["policies"].values():
        assert policy["sla_violation_rate"] >= bound


def test_point_baselines_obey_same_dwell_and_do_not_read_future():
    config = RiskConfig(min_hold_samples=3)
    values = np.array([[50.0, 60.0], [90.0, 30.0], [90.0, 30.0], [90.0, 30.0]])
    choices = point_choices(np.arange(4), values, config)
    np.testing.assert_array_equal(choices, [0, 0, 0, 1])
    values = np.arange(40, dtype=float).reshape(20, 2) + 10
    origins = np.arange(2, 20)
    before = causal_ewma(values, origins)
    changed = values.copy()
    changed[10:] += 900
    after = causal_ewma(changed, origins)
    np.testing.assert_array_equal(before[origins <= 10], after[origins <= 10])
