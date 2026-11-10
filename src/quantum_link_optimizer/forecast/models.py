"""Baselines and a small quantile network, both calibrated on validation."""

import copy

import numpy as np
import torch
from torch import nn

QUANTILES = (0.1, 0.5, 0.9)


class Prediction:
    def __init__(self, low, median, high, probability):
        self.low = low
        self.median = median
        self.high = high
        self.probability = probability


def rate(values, default):
    return float(np.mean(values)) if len(values) else default


class PointBaseline:
    """Persistence, EWMA or the same time yesterday, plus a train error band."""

    def __init__(self, name, column):
        self.name = name
        self.column = column

    def reference(self, frame, rows, h_index):
        name = self.column.format(h=frame.horizons[h_index])
        return frame.x[rows, frame.names.index(name)]

    def fit(self, frame, rows):
        self.bands = []

        for index in range(len(frame.horizons)):
            reference = self.reference(frame, rows, index)
            actual = frame.value[rows, index]
            breach = frame.breach[rows, index]
            known = np.isfinite(reference) & np.isfinite(actual) & (reference > 0)
            ratio = np.log(actual[known]) - np.log(reference[known])
            band = np.quantile(ratio, QUANTILES)

            # How often a breach follows when the reference is above the target
            above = reference > frame.target_ms
            seen = np.isfinite(breach) & np.isfinite(reference)
            self.bands.append(
                (
                    band,
                    rate(breach[seen & above], 1.0),
                    rate(breach[seen & ~above], 0.0),
                )
            )

        return self

    def predict(self, frame, rows):
        out = []

        for index in range(len(frame.horizons)):
            reference = self.reference(frame, rows, index)
            band, p_above, p_below = self.bands[index]
            low, median, high = (reference * np.exp(q) for q in band)
            probability = np.where(reference > frame.target_ms, p_above, p_below)
            out.append(Prediction(low, median, high, probability))

        return out


class QuantileNet(nn.Module):
    """Learns a correction to the log of the recent median, not the value."""

    def __init__(self, inputs, horizons, hidden=64):
        super().__init__()
        self.horizons = horizons
        self.body = nn.Sequential(
            nn.Linear(inputs, hidden),
            nn.ReLU(),
            nn.Linear(hidden, hidden),
            nn.ReLU(),
        )
        self.head = nn.Linear(hidden, horizons * 4)

    def forward(self, x, reference):
        out = self.head(self.body(x)).view(-1, self.horizons, 4)
        median = reference[:, None] + out[..., 1]

        # Positive gaps keep the quantiles ordered
        low = median - nn.functional.softplus(out[..., 0])
        high = median + nn.functional.softplus(out[..., 2])
        return torch.stack([low, median, high], dim=-1), out[..., 3]


def pinball(predicted, actual, mask):
    loss = 0.0

    for index, q in enumerate(QUANTILES):
        error = actual - predicted[..., index]
        loss = loss + torch.maximum(q * error, (q - 1) * error)

    return (loss * mask).sum() / mask.sum().clamp(min=1)


class Learned:
    name = "model"

    def __init__(self, epochs=40, seed=42, hidden=64):
        self.epochs = epochs
        self.seed = seed
        self.hidden = hidden

    def reference(self, frame, rows):
        recent = frame.x[rows, frame.names.index("rtt_median_5m")]
        last = frame.x[rows, frame.names.index("rtt_last")]
        value = np.where(np.isfinite(recent), recent, last)
        value = np.where(np.isfinite(value) & (value > 0), value, self.fallback)
        return torch.tensor(np.log(value), dtype=torch.float32)

    def tensors(self, frame, rows, scaler):
        x = torch.from_numpy(scaler.transform(frame.x[rows]))
        value = frame.value[rows]
        breach = frame.breach[rows]
        value_mask = np.isfinite(value) & (np.nan_to_num(value) > 0)
        log_value = np.log(np.where(value_mask, value, 1.0))
        return (
            x,
            self.reference(frame, rows),
            torch.tensor(log_value, dtype=torch.float32),
            torch.tensor(value_mask, dtype=torch.float32),
            torch.tensor(np.nan_to_num(breach), dtype=torch.float32),
            torch.tensor(np.isfinite(breach), dtype=torch.float32),
        )

    def loss(self, batch):
        x, reference, value, value_mask, breach, breach_mask = batch
        quantiles, logits = self.network(x, reference)
        bce = nn.functional.binary_cross_entropy_with_logits(
            logits, breach, weight=breach_mask, reduction="sum"
        ) / breach_mask.sum().clamp(min=1)
        return pinball(quantiles, value, value_mask) + bce

    def fit(self, frame, train, validation, scaler):
        torch.manual_seed(self.seed)
        torch.set_num_threads(1)
        known = frame.value[train]
        self.fallback = float(np.nanmedian(known[np.isfinite(known)]))
        train_set = self.tensors(frame, train, scaler)
        validation_set = self.tensors(frame, validation, scaler)
        self.network = QuantileNet(
            train_set[0].shape[1], len(frame.horizons), self.hidden
        )
        optimizer = torch.optim.Adam(self.network.parameters(), lr=1e-3)
        best, state, self.history = float("inf"), None, []
        generator = torch.Generator().manual_seed(self.seed)

        for epoch in range(self.epochs):
            self.network.train()
            order = torch.randperm(len(train_set[0]), generator=generator)

            for chunk in order.split(512):
                optimizer.zero_grad()
                loss = self.loss([item[chunk] for item in train_set])
                loss.backward()
                optimizer.step()

            self.network.eval()

            with torch.no_grad():
                score = float(self.loss(validation_set))

            self.history.append({"epoch": epoch + 1, "validation_loss": score})

            if score < best:
                best, state = score, copy.deepcopy(self.network.state_dict())

        self.network.load_state_dict(state)
        self.scaler = scaler
        return self

    def predict(self, frame, rows):
        with torch.no_grad():
            quantiles, logits = self.network(
                torch.from_numpy(self.scaler.transform(frame.x[rows])),
                self.reference(frame, rows),
            )

        quantiles = np.exp(quantiles.numpy())
        probability = torch.sigmoid(logits).numpy()
        return [
            Prediction(
                quantiles[:, i, 0],
                quantiles[:, i, 1],
                quantiles[:, i, 2],
                probability[:, i],
            )
            for i in range(len(frame.horizons))
        ]


def conformal(predictions, frame, rows, coverage=0.8):
    """Widen the band so that it covers `coverage` of validation outcomes."""
    widths = []

    for index, prediction in enumerate(predictions):
        actual = frame.value[rows, index]
        known = np.isfinite(actual) & np.isfinite(prediction.low)
        log_actual = np.log(actual[known])
        score = np.maximum(
            np.log(prediction.low[known]) - log_actual,
            log_actual - np.log(prediction.high[known]),
        )
        level = min(1.0, coverage * (1 + 1 / max(len(score), 1)))
        widths.append(float(np.quantile(score, level)) if len(score) else 0.0)

    return widths


def widen(predictions, widths):
    return [
        Prediction(
            p.low * np.exp(-width),
            p.median,
            p.high * np.exp(width),
            p.probability,
        )
        for p, width in zip(predictions, widths, strict=True)
    ]


def platt(probability, outcome):
    """Logistic recalibration of a probability on validation outcomes."""
    known = np.isfinite(outcome)
    p = np.clip(probability[known], 1e-6, 1 - 1e-6)
    x = np.log(p / (1 - p))
    y = outcome[known]
    a, b = 1.0, 0.0

    for _ in range(100):
        z = 1 / (1 + np.exp(-np.clip(a * x + b, -50, 50)))
        gradient = np.array([np.sum((z - y) * x), np.sum(z - y)])
        weight = z * (1 - z) + 1e-9
        hessian = np.array(
            [
                [np.sum(weight * x * x), np.sum(weight * x)],
                [np.sum(weight * x), np.sum(weight)],
            ]
        )
        step = np.linalg.solve(hessian + 1e-6 * np.eye(2), gradient)
        a, b = a - step[0], b - step[1]

        if np.abs(step).max() < 1e-8:
            break

    return a, b


def recalibrate(predictions, coefficients):
    result = []

    for prediction, (a, b) in zip(predictions, coefficients, strict=True):
        p = np.clip(prediction.probability, 1e-6, 1 - 1e-6)
        z = np.clip(a * np.log(p / (1 - p)) + b, -50, 50)
        result.append(
            Prediction(
                prediction.low, prediction.median, prediction.high, 1 / (1 + np.exp(-z))
            )
        )

    return result
