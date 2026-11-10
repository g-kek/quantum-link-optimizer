"""Online detection of a level step in path RTT, two-sided CUSUM."""

from collections import deque

import numpy as np


class StepDetector:
    def __init__(self, threshold=6.0, drift=0.5, warmup=8, min_step_ms=10.0):
        self.threshold = threshold
        self.drift = drift
        self.warmup = warmup
        self.min_step_ms = min_step_ms
        self.reference = deque(maxlen=32)
        self.up, self.down = 0.0, 0.0
        self.run_up, self.run_down = [], []

    def scale(self):
        values = np.asarray(self.reference)
        spread = 1.4826 * float(np.median(np.abs(values - np.median(values))))
        return max(spread, 0.05 * float(np.median(values)), 1.0)

    def update(self, value):
        """Feed one RTT, returns the step when the level has moved"""
        if len(self.reference) < self.warmup:
            self.reference.append(value)
            return None

        level = float(np.median(self.reference))
        z = (value - level) / self.scale()
        self.up = max(0.0, self.up + z - self.drift)
        self.down = max(0.0, self.down - z - self.drift)
        self.run_up = [*self.run_up, value] if self.up else []
        self.run_down = [*self.run_down, value] if self.down else []

        if max(self.up, self.down) < self.threshold:
            self.reference.append(value)
            return None

        run = self.run_up if self.up >= self.threshold else self.run_down
        after = float(np.median(run))
        self.up, self.down = 0.0, 0.0
        self.run_up, self.run_down = [], []

        if abs(after - level) < max(self.min_step_ms, 0.25 * min(level, after)):
            self.reference.append(value)
            return None

        # Start a new reference at the new level
        self.reference.clear()
        self.reference.extend(run)
        return {"before_ms": level, "after_ms": after}
