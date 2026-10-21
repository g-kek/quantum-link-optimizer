"""Purged multihorizon forecasting; all inputs end strictly before origin t."""

import copy
import csv
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
from torch import nn

from quantum_link_optimizer.telemetry import SOURCE, generate_telemetry, write_telemetry

HORIZONS = (1, 5, 15, 60)


@dataclass
class WarningData:
    x: np.ndarray
    events: np.ndarray
    rtt: np.ndarray
    indices: np.ndarray
    mean: np.ndarray
    scale: np.ndarray
    train_end: int
    val_end: int
    size: int
    horizons: tuple[int, ...]

    def mask(self, split: str) -> np.ndarray:
        start, end = {
            "train": (0, self.train_end),
            "val": (self.train_end, self.val_end),
            "test": (self.val_end, self.size),
        }[split]
        return (self.indices >= start) & (self.indices + max(self.horizons) <= end)


def warning_data(values, window=16, horizons=HORIZONS, sla_ms=100.0):
    values = np.asarray(values, dtype=float)
    if (
        values.ndim != 2
        or values.shape[1] != 2
        or not np.isfinite(values).all()
        or (values <= 0).any()
    ):
        raise ValueError("Expected finite positive two-path RTT")
    if not horizons or any(not isinstance(h, int) or h < 1 for h in horizons):
        raise ValueError("Horizons must be positive integer sample counts")
    if not np.isfinite(sla_ms) or sla_ms <= 0:
        raise ValueError("SLA must be finite and positive")
    train_end, val_end = int(len(values) * 0.6), int(len(values) * 0.8)
    if not 1 <= window < train_end:
        raise ValueError("Invalid history window")
    indices = np.arange(window, len(values) - max(horizons) + 1)
    mean = values[:train_end].mean(axis=0)
    scale = np.maximum(values[:train_end].std(axis=0), 1e-6)
    data = WarningData(
        np.stack([(values[t - window : t] - mean) / scale for t in indices]).astype(
            np.float32
        ),
        np.asarray(
            [
                [(values[t : t + h] > sla_ms).any(axis=0) for h in horizons]
                for t in indices
            ],
            dtype=np.float32,
        ),
        values[indices],
        indices,
        mean,
        scale,
        train_end,
        val_end,
        len(values),
        tuple(horizons),
    )
    if any(not data.mask(s).any() for s in ("train", "val", "test")):
        raise ValueError("Each split needs at least one fully contained horizon target")
    return data


class WarningForecaster(nn.Module):
    def __init__(self, window=16, horizons=HORIZONS):
        super().__init__()
        self.horizons = tuple(horizons)
        self.network = nn.Sequential(
            nn.Flatten(),
            nn.Linear(window * 2, 32),
            nn.ReLU(),
            nn.Linear(32, 2 + 2 * len(horizons)),
        )

    def forward(self, x):
        output = self.network(x)
        return x[:, -1] + output[:, :2], output[:, 2:].reshape(
            -1, len(self.horizons), 2
        )


def train_warning(data, seed=42, epochs=40):
    if epochs < 1:
        raise ValueError("Epochs must be positive")
    torch.set_num_threads(1)
    torch.manual_seed(seed)
    torch.use_deterministic_algorithms(True)
    model = WarningForecaster(data.x.shape[1], data.horizons)
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.001, weight_decay=0.001)
    x = torch.from_numpy(data.x)
    y = torch.tensor((data.rtt - data.mean) / data.scale, dtype=torch.float32)
    events = torch.from_numpy(data.events)
    train = torch.tensor(np.flatnonzero(data.mask("train")))
    val = torch.tensor(np.flatnonzero(data.mask("val")))

    def loss_on(rows):
        rtt, logits = model(x[rows])
        # Equal head weights after RTT standardization; no class balancing.
        return nn.functional.mse_loss(
            rtt, y[rows]
        ) + nn.functional.binary_cross_entropy_with_logits(logits, events[rows])

    best, state, history = float("inf"), copy.deepcopy(model.state_dict()), []
    for epoch in range(epochs):
        model.train()
        for batch in train[torch.randperm(len(train))].split(128):
            optimizer.zero_grad()
            loss = loss_on(batch)
            loss.backward()
            optimizer.step()
        model.eval()
        with torch.no_grad():
            validation = float(loss_on(val))
        history.append({"epoch": epoch + 1, "val_loss": validation})
        if validation < best:
            best, state = validation, copy.deepcopy(model.state_dict())
    model.load_state_dict(state)
    model.eval()
    return model, history


def warning_predict(model, x, mean, scale):
    with torch.no_grad():
        rtt, logits = model(torch.as_tensor(x, dtype=torch.float32))
    return rtt.numpy() * scale + mean, torch.sigmoid(logits).numpy()


def alarm_metrics(values, indices, probability, horizon, threshold=0.5, sla_ms=100.0):
    """Match rising alarm episodes to first unmatched eligible onset, per path."""
    alarms, matched, eligible, leads = 0, 0, 0, []
    for channel in range(2):
        bad = values[:, channel] > sla_ms
        onsets = np.flatnonzero(bad & ~np.r_[False, bad[:-1]])
        available = indices[~bad[indices - 1]]
        events = [
            int(j)
            for j in onsets
            if np.any((available <= j) & (j < available + horizon))
        ]
        eligible += len(events)
        used = set()
        active = False
        for t, p in zip(indices, probability[:, channel], strict=True):
            firing = p >= threshold and not bad[t - 1]
            if firing and not active:
                alarms += 1
                candidates = [
                    j for j in events if t <= j < t + horizon and j not in used
                ]
                if candidates:
                    j = min(candidates)
                    used.add(j)
                    matched += 1
                    leads.append(int(j - (t - 1)))
            active = firing
    opportunities = len(indices) * 2
    return {
        "alarm_episodes": alarms,
        "matched_events": matched,
        "eligible_onsets": eligible,
        "precision": matched / alarms if alarms else None,
        "recall": matched / eligible if eligible else None,
        "false_alarm_episodes": alarms - matched,
        "false_alarms_per_1000_path_origins": 1000 * (alarms - matched) / opportunities,
        "path_origins": opportunities,
        "mean_lead_samples_from_last_observation": float(np.mean(leads))
        if leads
        else None,
        "lead_samples": leads,
        "no_events": eligible == 0,
    }


def dump_json(path, report):
    path.write_text(
        json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )


def run_warning(output: Path, samples=4000, seed=42, epochs=40, values=None):
    values = generate_telemetry(samples, seed) if values is None else values
    data = warning_data(values)
    model, history = train_warning(data, seed, epochs)
    rtt, probability = warning_predict(model, data.x, data.mean, data.scale)
    test = data.mask("test")
    indices = data.indices[test]
    output.mkdir(parents=True, exist_ok=True)
    write_telemetry(output / "telemetry.csv", values)
    checkpoint = {
        "fit_prefix_sha256": hashlib.sha256(
            np.ascontiguousarray(values[: data.val_end]).tobytes()
        ).hexdigest(),
        "state_dict": model.state_dict(),
        "window": data.x.shape[1],
        "horizons": list(data.horizons),
        "mean": data.mean.tolist(),
        "scale": data.scale.tolist(),
        "source": SOURCE,
        "train_end": data.train_end,
        "val_end": data.val_end,
        "samples": len(values),
        "sla_ms": 100.0,
        "sample_interval_seconds": 1.0,
        "threshold": 0.5,
        "seed": seed,
    }
    torch.save(checkpoint, output / "warning-model.pt")
    report = {
        "source": SOURCE,
        "seed": seed,
        "sample_interval_seconds": 1.0,
        "event": "Any RTT > 100 ms in [t, t+h); inputs end at t-1; h is samples",
        "threshold": 0.5,
        "threshold_source": "predeclared configuration, not test",
        "probability_warning": "Uncalibrated model output, not confidence",
        "splits": {},
        "horizons": {},
        "training": history,
    }
    for name in ("train", "val", "test"):
        rows = data.indices[data.mask(name)]
        report["splits"][name] = {
            "first_origin": int(rows[0]),
            "last_origin": int(rows[-1]),
            "last_label_row": int(rows[-1] + max(data.horizons) - 1),
            "origins": len(rows),
        }
    for k, h in enumerate(data.horizons):
        target = data.events[test, k]
        frequency = data.events[data.mask("train"), k].mean(axis=0)
        persistence = (values[indices - 1] > 100).astype(float)
        report["horizons"][str(h)] = {
            "observed_event_rate": target.mean(axis=0).tolist(),
            "model_brier": float(np.mean((probability[test, k] - target) ** 2)),
            "persistence_brier": float(np.mean((persistence - target) ** 2)),
            "train_frequency_brier": float(np.mean((frequency - target) ** 2)),
            "model_warning": alarm_metrics(values, indices, probability[test, k], h),
            "frequency_warning": alarm_metrics(
                values, indices, np.broadcast_to(frequency, target.shape), h
            ),
        }
    with (output / "warning-predictions.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "sample",
                "horizon_samples",
                "path",
                "probability",
                "event",
                "rtt_prediction_ms",
            ]
        )
        for i in np.flatnonzero(test):
            for k, h in enumerate(data.horizons):
                for channel in range(2):
                    writer.writerow(
                        [
                            data.indices[i],
                            h,
                            channel,
                            probability[i, k, channel],
                            data.events[i, k, channel],
                            rtt[i, channel],
                        ]
                    )
    dump_json(output / "warning-report.json", report)
    return report
