"""Small two-head MLP with train-only preprocessing and inspectable JSON weights."""

import math

import numpy as np
import torch
from torch import nn

from quantum_link_optimizer.experiments.common import write_json

from .features import FEATURES, exhaustion_seconds


class Forecast:
    def __init__(self, median, mean, scale):
        self.median = np.asarray(median, dtype=float)
        self.mean = np.asarray(mean, dtype=float)
        self.scale = np.asarray(scale, dtype=float)
        self.network = nn.Sequential(
            nn.Linear(len(FEATURES) * 2, 12), nn.ReLU(), nn.Linear(12, 2)
        )

    def transform(self, vectors):
        values = np.array(vectors, dtype=float)

        if values.ndim != 2 or values.shape[1] != len(FEATURES):
            raise ValueError("invalid forecast feature dimensions")

        if np.isinf(values).any():
            raise ValueError("infinite forecast feature")

        missing = np.isnan(values)
        filled = np.where(missing, self.median, values)
        scaled = (filled - self.mean) / self.scale

        return torch.tensor(
            np.concatenate([scaled, missing], axis=1), dtype=torch.float32
        )

    def predict(self, vectors):
        self.network.eval()

        with torch.no_grad():
            values = torch.sigmoid(self.network(self.transform(vectors)))

        return values.numpy().tolist()

    def save(self, path, training_episodes):
        write_json(
            path,
            {
                "schema_version": 1,
                "feature_names": list(FEATURES),
                "targets": ["late_command", "empty_key_pool"],
                "median": self.median.tolist(),
                "mean": self.mean.tolist(),
                "scale": self.scale.tolist(),
                "training_episodes": sorted(training_episodes),
                "weights": {
                    k: v.tolist() for k, v in self.network.state_dict().items()
                },
            },
        )

    @classmethod
    def load(cls, path):
        import json

        value = json.loads(path.read_text())

        if value["schema_version"] != 1 or value["feature_names"] != list(FEATURES):
            raise ValueError("incompatible forecast schema")

        for name in ("median", "mean", "scale"):
            if len(value[name]) != len(FEATURES) or not all(
                math.isfinite(x) for x in value[name]
            ):
                raise ValueError("invalid preprocessing")

        if any(x <= 0 for x in value["scale"]):
            raise ValueError("invalid forecast scale")

        model = cls(value["median"], value["mean"], value["scale"])
        weights = {
            k: torch.tensor(v, dtype=torch.float32) for k, v in value["weights"].items()
        }

        if any(not torch.isfinite(v).all() for v in weights.values()):
            raise ValueError("invalid model weights")

        model.network.load_state_dict(weights)
        model.network.eval()

        return model


def fit(rows, seed=42, epochs=80):
    if not rows:
        raise ValueError("training data is empty")

    torch.set_num_threads(1)
    torch.manual_seed(seed)
    raw = np.array([r["features"] for r in rows], dtype=float)
    median = np.array(
        [
            float(np.median(column[~np.isnan(column)]))
            if np.any(~np.isnan(column))
            else 0
            for column in raw.T
        ]
    )
    filled = np.where(np.isnan(raw), median, raw)
    scale = np.maximum(filled.std(axis=0), 1e-6)
    model = Forecast(median, filled.mean(axis=0), scale)
    x = model.transform(raw)
    targets = np.array([r["targets"] for r in rows], dtype=float)
    mask = torch.tensor(~np.isnan(targets), dtype=torch.float32)
    y = torch.tensor(np.nan_to_num(targets), dtype=torch.float32)
    optimizer = torch.optim.Adam(model.network.parameters(), lr=0.01)
    criterion = nn.BCEWithLogitsLoss(reduction="none")

    if not mask.sum():
        raise ValueError("training labels are absent")

    for _ in range(epochs):
        optimizer.zero_grad()
        loss = (criterion(model.network(x), y) * mask).sum() / mask.sum()
        loss.backward()
        optimizer.step()

    model.network.eval()

    return model


def evaluate(model, training, validation):
    predicted = model.predict([r["features"] for r in validation])
    result = {}

    for index, name in enumerate(("late_command", "empty_key_pool")):
        train_targets = [
            r["targets"][index] for r in training if r["targets"][index] is not None
        ]
        selected = [
            (r["targets"][index], p[index])
            for r, p in zip(validation, predicted, strict=True)
            if r["targets"][index] is not None
        ]
        constant = sum(train_targets) / len(train_targets) if train_targets else None
        result[name] = {
            "count": len(selected),
            "brier": sum((y - p) ** 2 for y, p in selected) / len(selected)
            if selected
            else None,
            "train_frequency_brier": sum((y - constant) ** 2 for y, _ in selected)
            / len(selected)
            if selected and constant is not None
            else None,
            "positive_fraction": sum(y for y, _ in selected) / len(selected)
            if selected
            else None,
        }

    for index, name in enumerate(("late_command", "empty_key_pool")):
        errors = []

        for row in validation:
            target = row["targets"][index]
            fields = dict(zip(FEATURES, row["features"], strict=True))
            score = None

            if index == 0 and fields["ewma_rtt_ms"] is not None:
                score = float(fields["ewma_rtt_ms"] > 100)

            if index == 1 and fields["kms_available_keys"] is not None:
                seconds = exhaustion_seconds(
                    fields["kms_available_keys"],
                    fields["kms_demand_per_s"],
                    fields["kms_refill_per_s"],
                )
                score = float(seconds is not None and seconds <= 0.25)

            if target is not None and score is not None:
                errors.append((target - score) ** 2)

        result[name]["rule_brier"] = sum(errors) / len(errors) if errors else None
        result[name]["rule_coverage"] = len(errors)

    return result
