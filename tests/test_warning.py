import json

import numpy as np
import pytest
import torch

from quantum_link_optimizer.telemetry import generate_telemetry
from quantum_link_optimizer.warning import (
    alarm_metrics,
    run_warning,
    train_warning,
    warning_data,
    warning_predict,
)


def test_purged_labels_and_causal_features():
    values = generate_telemetry(500)
    data = warning_data(values)
    for split, end in (("train", 300), ("val", 400), ("test", 500)):
        origins = data.indices[data.mask(split)]
        assert origins[-1] + 60 == end
        for t in origins:
            row = np.flatnonzero(data.indices == t)[0]
            for k, h in enumerate(data.horizons):
                np.testing.assert_array_equal(
                    data.events[row, k], (values[t : t + h] > 100).any(axis=0)
                )
    changed = values.copy()
    changed[350:] += 300
    other = warning_data(changed)
    row = np.flatnonzero(data.indices == 350)[0]
    np.testing.assert_array_equal(data.x[: row + 1], other.x[: row + 1])
    np.testing.assert_array_equal(data.mean, other.mean)
    np.testing.assert_array_equal(data.scale, other.scale)


def test_test_isolation_and_determinism():
    values = generate_telemetry(500)
    data = warning_data(values)
    first, history = train_warning(data, 7, 3)
    values[data.val_end :] += 800
    changed = warning_data(values)
    second, other = train_warning(changed, 7, 3)
    assert history == other
    for key, tensor in first.state_dict().items():
        assert torch.equal(tensor, second.state_dict()[key])
    before, _ = warning_predict(first, data.x, data.mean, data.scale)
    after, _ = warning_predict(second, changed.x, changed.mean, changed.scale)
    np.testing.assert_array_equal(
        before[data.indices <= data.val_end], after[data.indices <= data.val_end]
    )


def test_alarm_matching_and_no_events():
    values = np.full((30, 2), 50.0)
    values[15:18, 0] = 110
    indices = np.arange(5, 26)
    probabilities = np.zeros((len(indices), 2))
    probabilities[indices == 12, 0] = 1
    probabilities[indices == 14, 0] = 1
    probabilities[indices == 7, 1] = 1
    metrics = alarm_metrics(values, indices, probabilities, 5)
    assert metrics["alarm_episodes"] == 3
    assert metrics["matched_events"] == 1
    assert metrics["eligible_onsets"] == 1
    assert metrics["lead_samples"] == [4]
    assert metrics["false_alarm_episodes"] == 2
    empty = alarm_metrics(np.full_like(values, 50), indices, probabilities, 5)
    assert empty["recall"] is None and empty["no_events"]
    assert empty["mean_lead_samples_from_last_observation"] is None


def test_warning_artifacts(tmp_path):
    report = run_warning(tmp_path, samples=500, epochs=2)
    assert json.loads((tmp_path / "warning-report.json").read_text()) == report
    assert (tmp_path / "warning-predictions.csv").is_file()
    checkpoint = torch.load(tmp_path / "warning-model.pt", weights_only=True)
    assert checkpoint["horizons"] == [1, 5, 15, 60]
    with pytest.raises(ValueError):
        warning_data(generate_telemetry(100))
