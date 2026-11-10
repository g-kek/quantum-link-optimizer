import json

import numpy as np
import pytest

from quantum_link_optimizer.forecast import generator
from quantum_link_optimizer.forecast.features import build, future, trailing
from quantum_link_optimizer.forecast.metrics import auc, warnings
from quantum_link_optimizer.forecast.models import Prediction, conformal, platt
from quantum_link_optimizer.forecast.run import run_forecast


def test_trailing_window_uses_only_the_past():
    values = np.arange(10.0)
    result = trailing(values, 3, np.nanmedian)

    assert np.isnan(result[:2]).all()
    assert result[2] == 1.0 and result[9] == 8.0


def test_targets_lie_after_the_origin():
    rtt = np.array([10.0, 10.0, 200.0, 200.0, 10.0])
    value, breach = future(rtt, (1, 2), target_ms=100)

    assert value[1, 0] == 200.0
    assert breach[0, 0] == 0 and breach[1, 0] == 1
    assert np.isnan(value[-1, 0]) and np.isnan(breach[-1, 1])


def test_features_never_see_the_future():
    rtt = np.full(400, 50.0)
    rtt[300:] = 500.0
    series = {
        "A": {
            "rtt": rtt,
            "load": np.full(400, 0.5),
            "loss": np.zeros(400),
            "keys": np.full(400, 100.0),
            "time_s": np.arange(400) * 10.0,
        }
    }
    frame = build(series, (6,), 100, 10)
    last = frame.names.index("rtt_median_15m")

    assert frame.x[299, last] == 50.0
    assert frame.breach[299, 0] == 1


def test_generator_marks_foreseeable_and_sudden_episodes():
    week = generator.generate(days=3, seed=1)
    kinds = set(week.episode.ravel()) - {""}

    assert {"load", "sudden"} <= kinds
    assert week.rtt_ms.shape == (3 * generator.DAY, 2)


def test_conformal_width_reaches_the_coverage():
    rng = np.random.default_rng(0)
    actual = np.exp(rng.normal(4, 0.5, 2000))
    narrow = Prediction(
        np.full(2000, 50.0), np.full(2000, 55.0), np.full(2000, 60.0), None
    )
    frame = type("F", (), {"value": actual[:, None]})()
    width = conformal([narrow], frame, np.arange(2000), coverage=0.8)[0]
    low, high = 50 * np.exp(-width), 60 * np.exp(width)

    assert np.mean((actual >= low) & (actual <= high)) == pytest.approx(0.8, abs=0.02)


def test_platt_fixes_an_overconfident_probability():
    rng = np.random.default_rng(1)
    truth = rng.uniform(0, 1, 5000)
    outcome = (rng.uniform(0, 1, 5000) < truth).astype(float)
    overconfident = np.clip(truth * 1.6 - 0.3, 0.01, 0.99)
    a, b = platt(overconfident, outcome)
    p = 1 / (1 + np.exp(-(a * np.log(overconfident / (1 - overconfident)) + b)))

    assert np.mean((p - outcome) ** 2) < np.mean((overconfident - outcome) ** 2)


def test_auc_orders_positives_first():
    assert auc(np.array([0.9, 0.8, 0.1]), np.array([1, 1, 0])) == 1.0


def test_warning_lead_is_counted_before_the_onset():
    rtt = np.full(200, 50.0)
    rtt[100:] = 300.0
    probability = np.zeros(200)
    probability[90:] = 0.9
    episode = np.full(200, "load", dtype=object)
    result = warnings(probability, rtt, episode, horizon=20, step_s=10, target_ms=100)

    # The median of the last minute crosses the target at sample 102,
    # the alarm has been on since sample 90
    assert result["warned"] == 1
    assert result["median_lead_s"] == 120.0
    assert result["false_alarms_per_day"] == 0


def test_short_run_writes_a_report(tmp_path):
    report = run_forecast(tmp_path, days=4, epochs=2, horizons_min=(1, 5))
    saved = json.loads((tmp_path / "forecast-report.json").read_text())

    assert set(report["results"]) == {"model", "persistence", "median_5m", "yesterday"}
    assert saved["horizons_s"] == [60, 300]
    assert (tmp_path / "index.html").exists()
