import copy

import numpy as np
import torch

from quantum_link_optimizer.experiments.forecasting.model import fit
from quantum_link_optimizer.experiments.recurrent.model import (
    SequenceForecast,
    fit_sequences,
    sequences,
)


def rows(episode="train", n=5):
    return [
        {
            "episode_id": episode,
            "path": path,
            "decision_ns": i + 1,
            "features": [float(i)] * 15,
            "targets": [i % 2, 0],
        }
        for i in range(n)
        for path in ("A", "B")
    ]


def test_history_is_past_only_and_resets_across_paths_and_episodes():
    source = rows() + rows("other")
    before = sequences(source, 3)
    changed = copy.deepcopy(source)
    changed[8]["features"] = [999.0] * 15
    after = sequences(changed, 3)

    def prefix(items):
        return [r for r in items if r["decision_ns"] < 5]

    assert prefix(before) == prefix(after)
    assert all(len(r["sequence"]) == 1 for r in before if r["decision_ns"] == 1)
    assert all(len(r["sequence"]) <= 3 for r in before)


def test_lstm_json_roundtrip_and_padding_do_not_change_short_predictions(tmp_path):
    source = rows()
    preprocessing = fit(source, epochs=2)
    model = fit_sequences(source, preprocessing, epochs=3)
    examples = sequences(source, 8)
    short, long = examples[0]["sequence"], examples[-1]["sequence"]
    alone = model.predict_sequences([short])[0]
    together = model.predict_sequences([short, long])[0]
    assert np.allclose(alone, together, atol=1e-6)
    path = tmp_path / "lstm.json"
    model.save(path, ["train"])
    loaded = SequenceForecast.load(path)
    assert np.allclose(
        loaded.predict_sequences([long]), model.predict_sequences([long])
    )
    assert not model.network.memory.bidirectional
    assert torch.isfinite(model.network(*model.inputs([long]))).all()
