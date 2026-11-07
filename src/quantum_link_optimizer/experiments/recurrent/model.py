"""Variable-length, past-only decision histories with train-only preprocessing."""

import json
from collections import defaultdict, deque

import numpy as np
import torch
from torch import nn
from torch.nn.utils.rnn import pack_padded_sequence, pad_sequence

from quantum_link_optimizer.experiments.common import write_json
from quantum_link_optimizer.experiments.forecasting.features import FEATURES, vector
from quantum_link_optimizer.experiments.forecasting.model import Forecast
from quantum_link_optimizer.experiments.forecasting.policy import ForecastPolicy
from quantum_link_optimizer.selection.policy import Policy


def sequences(rows, window):
    histories = defaultdict(lambda: deque(maxlen=window))
    previous = {}
    result = []

    for row in sorted(
        rows, key=lambda r: (r["episode_id"], r["decision_ns"], r["path"])
    ):
        key = row["episode_id"], row["path"]
        now = row["decision_ns"]

        if now <= previous.get(key, -1):
            raise ValueError("duplicate or unordered decision time")

        previous[key] = now
        histories[key].append(row["features"])
        result.append({**row, "sequence": list(histories[key])})

    return result


class RecurrentNetwork(nn.Module):
    def __init__(self, hidden_size):
        super().__init__()
        self.memory = nn.LSTM(len(FEATURES) * 2, hidden_size, batch_first=True)
        self.head = nn.Linear(hidden_size, 2)

    def forward(self, padded, lengths):
        packed = pack_padded_sequence(
            padded, lengths.cpu(), batch_first=True, enforce_sorted=False
        )
        _, (hidden, _) = self.memory(packed)
        return self.head(hidden[-1])


class SequenceForecast(Forecast):
    def __init__(self, median, mean, scale, window=8, hidden_size=16):
        if type(window) is not int or not 1 <= window <= 64:
            raise ValueError("invalid history window")

        if type(hidden_size) is not int or not 1 <= hidden_size <= 128:
            raise ValueError("invalid hidden size")

        super().__init__(median, mean, scale)
        self.window = window
        self.hidden_size = hidden_size
        self.network = RecurrentNetwork(hidden_size)

    def inputs(self, histories):
        if not histories or any(not 1 <= len(h) <= self.window for h in histories):
            raise ValueError("invalid sequence length")

        tensors = [self.transform(history) for history in histories]
        return pad_sequence(tensors, batch_first=True), torch.tensor(
            [len(t) for t in tensors]
        )

    def predict(self, vectors):
        return self.predict_sequences([[v] for v in vectors])

    def predict_sequences(self, histories):
        self.network.eval()

        with torch.no_grad():
            probabilities = torch.sigmoid(self.network(*self.inputs(histories)))

        return probabilities.numpy().tolist()

    def save(self, path, training_episodes):
        super().save(path, training_episodes)
        value = json.loads(path.read_text())
        value.update(
            architecture="lstm", window=self.window, hidden_size=self.hidden_size
        )
        write_json(path, value)

    @classmethod
    def load(cls, path):
        value = json.loads(path.read_text())

        if (
            value.get("architecture") != "lstm"
            or value["schema_version"] != 1
            or value["feature_names"] != list(FEATURES)
        ):
            raise ValueError("incompatible LSTM schema")

        for name in ("median", "mean", "scale"):
            values = np.asarray(value[name], dtype=float)

            if values.shape != (len(FEATURES),) or not np.isfinite(values).all():
                raise ValueError("invalid LSTM preprocessing")

        if min(value["scale"]) <= 0:
            raise ValueError("invalid LSTM scale")

        model = cls(
            value["median"],
            value["mean"],
            value["scale"],
            value["window"],
            value["hidden_size"],
        )
        weights = {k: torch.tensor(v) for k, v in value["weights"].items()}

        if any(not torch.isfinite(v).all() for v in weights.values()):
            raise ValueError("invalid LSTM weights")

        model.network.load_state_dict(weights)
        model.network.eval()
        return model


def fit_sequences(rows, preprocessing, *, window=8, hidden_size=16, epochs=80, seed=42):
    torch.set_num_threads(1)
    torch.manual_seed(seed)
    model = SequenceForecast(
        preprocessing.median,
        preprocessing.mean,
        preprocessing.scale,
        window,
        hidden_size,
    )
    examples = sequences(rows, window)
    x, lengths = model.inputs([row["sequence"] for row in examples])
    targets = np.asarray([row["targets"] for row in examples], dtype=float)
    mask = torch.tensor(~np.isnan(targets), dtype=torch.float32)
    y = torch.tensor(np.nan_to_num(targets), dtype=torch.float32)

    if not mask.sum() or epochs < 1:
        raise ValueError("missing labels or epochs")

    optimizer = torch.optim.Adam(model.network.parameters(), lr=0.01)
    criterion = nn.BCEWithLogitsLoss(reduction="none")

    for _ in range(epochs):
        model.network.train()
        optimizer.zero_grad()
        loss = (criterion(model.network(x, lengths), y) * mask).sum() / mask.sum()
        loss.backward()
        optimizer.step()

    model.network.eval()
    return model


class RecurrentPolicy(ForecastPolicy):
    def __init__(self, settings, model, penalty_ms=100):
        super().__init__(settings, model, penalty_ms)
        self.histories = {p: deque(maxlen=model.window) for p in ("A", "B")}

    def choose(self, snapshot, now_ns):
        for path in ("A", "B"):
            self.histories[path].append(vector(snapshot[path]))

        predictions = self.model.predict_sequences(
            [list(self.histories[path]) for path in ("A", "B")]
        )

        for path, values in zip(("A", "B"), predictions, strict=True):
            snapshot[path]["predicted_late_probability"] = values[0]
            snapshot[path]["predicted_empty_pool_probability"] = values[1]

        return Policy.choose(self, snapshot, now_ns)
