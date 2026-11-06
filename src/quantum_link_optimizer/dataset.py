"""Chronological windows with preprocessing fitted only on the training prefix."""

from dataclasses import dataclass

import numpy as np

from quantum_link_optimizer.telemetry import validate_telemetry


@dataclass
class Windows:
    x: np.ndarray
    y: np.ndarray
    indices: np.ndarray
    train_end: int
    val_end: int
    mean: np.ndarray
    scale: np.ndarray

    def mask(self, split: str) -> np.ndarray:
        if split == "train":
            return self.indices < self.train_end
        if split == "val":
            return (self.indices >= self.train_end) & (self.indices < self.val_end)
        if split == "test":
            return self.indices >= self.val_end
        raise ValueError(f"Unknown split: {split}")


def make_windows(values: np.ndarray, window: int = 16) -> Windows:
    validate_telemetry(values)
    train_end = int(len(values) * 0.6)
    val_end = int(len(values) * 0.8)
    if not 1 <= window < train_end:
        raise ValueError("Window must be positive and smaller than the training set")
    mean = values[:train_end].mean(axis=0)
    scale = np.maximum(values[:train_end].std(axis=0), 1e-6)
    indices = np.arange(window, len(values))
    x = np.stack([(values[t - window : t] - mean) / scale for t in indices])
    return Windows(
        x.astype(np.float32),
        values[indices].copy(),
        indices,
        train_end,
        val_end,
        mean,
        scale,
    )
