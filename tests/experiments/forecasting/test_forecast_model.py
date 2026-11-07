import numpy as np
import pytest

from quantum_link_optimizer.experiments.forecasting.features import (
    FEATURES,
    exhaustion_seconds,
)
from quantum_link_optimizer.experiments.forecasting.model import Forecast, fit


def test_train_only_preprocessing_and_json_roundtrip(tmp_path):
    rows = [
        {"features": [value] * len(FEATURES), "targets": [value % 2, None]}
        for value in range(4)
    ]
    model = fit(rows, epochs=3)
    before = model.mean.copy()
    model.predict([[1e6] * len(FEATURES)])
    assert np.array_equal(model.mean, before)
    model.save(tmp_path / "model.json", {"train-1"})
    restored = Forecast.load(tmp_path / "model.json")
    assert restored.predict([rows[0]["features"]]) == model.predict(
        [rows[0]["features"]]
    )

    with pytest.raises(ValueError, match="infinite"):
        restored.predict([[float("inf")] * len(FEATURES)])


def test_key_balance_distinguishes_unknown_empty_and_no_drain():
    assert exhaustion_seconds(None, 3, 1) is None
    assert exhaustion_seconds(0, 3, 1) == 0
    assert exhaustion_seconds(3, 1, 1) is None
    assert exhaustion_seconds(6, 3, 1) == 3
