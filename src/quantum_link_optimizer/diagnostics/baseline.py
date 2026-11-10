"""Robust running norm of a signal: median and MAD of recent normal values."""

from collections import deque

import numpy as np

MAD_SCALE = 1.4826


class Norm:
    def __init__(self, soft_floor, hard_limit, window=200, warmup=20, k=4.0):
        self.soft_floor = soft_floor
        self.hard_limit = hard_limit
        self.values = deque(maxlen=window)
        self.warmup = warmup
        self.k = k
        self.last_seen = None

    def observe(self, value, observed_ns):
        if value is None or observed_ns == self.last_seen:
            return

        self.last_seen = observed_ns

        # Values above the hard limit are faults, they must not become the norm
        if value < self.hard_limit:
            self.values.append(float(value))

    def threshold(self):
        if len(self.values) < self.warmup:
            return self.hard_limit

        values = np.asarray(self.values)
        median = float(np.median(values))
        spread = MAD_SCALE * float(np.median(np.abs(values - median)))

        # Key rotation makes some normal signals two-level, the upper level
        # must stay normal too, so the high quantile bounds the norm from below
        relative = max(
            median + self.k * max(spread, 0.05 * median),
            1.5 * float(np.quantile(values, 0.99)),
        )
        return min(self.hard_limit, max(self.soft_floor, relative))

    def state(self):
        return {
            "samples": len(self.values),
            "median": float(np.median(self.values)) if self.values else None,
            "threshold": self.threshold(),
            "learned": len(self.values) >= self.warmup,
        }


def norms(target_ms):
    """Soft floors are the old absolute rules, hard limits are budget shares."""
    receiver = {"soft_floor": 1e6, "hard_limit": 0.2 * target_ms * 1e6}
    return {
        "receiver_queue_wait_ns": Norm(**receiver),
        "receiver_key_wait_ns": Norm(**receiver),
        "receiver_processing_ns": Norm(**receiver),
        "last_key_wait_ms": Norm(10.0, 0.25 * target_ms),
        "kms_request_ms": Norm(10.0, 0.25 * target_ms),
    }
