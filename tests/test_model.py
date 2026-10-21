import csv
import json

import numpy as np
import torch

from quantum_link_optimizer.cli import run_experiment
from quantum_link_optimizer.dataset import make_windows
from quantum_link_optimizer.model import RTTForecaster, predict, train_model
from quantum_link_optimizer.telemetry import generate_telemetry, read_telemetry


def test_training_determinism_and_test_isolation():
    values = generate_telemetry(200)
    data = make_windows(values, 8)
    model, history = train_model(data, seed=7, epochs=3)
    changed = values.copy()
    changed[data.val_end :] += 400
    modified = make_windows(changed, 8)
    second, second_history = train_model(modified, seed=7, epochs=3)
    assert history == second_history
    for key, tensor in model.state_dict().items():
        assert torch.equal(tensor, second.state_dict()[key])
    forecast, probability = predict(model, data)
    changed_forecast, _ = predict(model, modified)
    index = np.flatnonzero(data.indices == data.val_end)[0]
    np.testing.assert_array_equal(forecast[: index + 1], changed_forecast[: index + 1])
    assert np.isfinite(forecast).all()
    assert ((probability >= 0) & (probability <= 1)).all()


def test_experiment_artifacts_and_checkpoint(tmp_path):
    report = run_experiment(tmp_path, samples=200, window=8, epochs=3)
    stored = json.loads((tmp_path / "report.json").read_text())
    assert stored == report
    assert stored["policies"]["nn_selector"]["samples"] == 40
    checkpoint = torch.load(tmp_path / "model.pt", weights_only=True)
    model = RTTForecaster(checkpoint["window"])
    model.load_state_dict(checkpoint["state_dict"])
    data = make_windows(read_telemetry(tmp_path / "telemetry.csv"), 8)
    np.testing.assert_array_equal(data.mean, checkpoint["mean"])
    forecast, probability = predict(model, data)
    with (tmp_path / "predictions.csv").open() as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 40
    assert int(rows[0]["sample"]) == 160
    np.testing.assert_allclose(
        [float(row["forecast_0_ms"]) for row in rows],
        forecast[data.mask("test"), 0],
    )
    np.testing.assert_allclose(
        [float(row["degradation_probability_1"]) for row in rows],
        probability[data.mask("test"), 1],
    )
