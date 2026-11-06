import math
import random
from collections import deque

DEFAULTS = {
    "available": True,
    "processing_delay_ms": 0,
    "rate_mbit": 100,
    "loss_pct": 0,
    "queue_limit_bytes": 65536,
    "temperature_c": 35,
}


def validate(settings):
    if set(settings) - DEFAULTS.keys():
        raise ValueError("unknown device setting")
    for name, value in settings.items():
        if name == "available":
            if type(value) is not bool:
                raise ValueError("available must be boolean")
        elif type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError(f"invalid device {name}")
        elif name != "temperature_c" and value < 0:
            raise ValueError(f"negative device {name}")
    if "rate_mbit" in settings and settings["rate_mbit"] <= 0:
        raise ValueError("device rate must be positive")
    if settings.get("loss_pct", 0) > 100:
        raise ValueError("invalid device loss percentage")
    if "queue_limit_bytes" in settings and (
        type(settings["queue_limit_bytes"]) is not int
        or settings["queue_limit_bytes"] < 1
    ):
        raise ValueError("queue limit must be a positive integer")


class Device:
    def __init__(self, settings=None, seed=0):
        self.settings = dict(DEFAULTS)
        self.random = random.Random(seed)
        self.generation = 0
        self.queues = {direction: deque() for direction in ("forward", "reverse")}
        self.counters = {
            "packets": 0,
            "error_drops": 0,
            "queue_drops": 0,
            "outage_drops": 0,
        }
        self.update(settings or {})

    def update(self, settings):
        validate(settings)
        if self.settings["available"] and settings.get("available") is False:
            self.generation += 1
            for queue in self.queues.values():
                queue.clear()
        self.settings.update(settings)

    def clean(self, direction, now):
        queue = self.queues[direction]
        while queue and queue[0][0] <= now:
            queue.popleft()
        return queue

    def transit(self, size, direction, now):
        if direction not in self.queues or type(size) is not int or size < 1:
            raise ValueError("invalid packet size or direction")
        self.counters["packets"] += 1
        if not self.settings["available"]:
            self.counters["outage_drops"] += 1
            return None
        if self.random.random() < self.settings["loss_pct"] / 100:
            self.counters["error_drops"] += 1
            return None
        queue = self.clean(direction, now)
        if (
            sum(length for _, length in queue) + size
            > self.settings["queue_limit_bytes"]
        ):
            self.counters["queue_drops"] += 1
            return None
        starts = max(now, queue[-1][0] if queue else now)
        departure = starts + size * 8 / (self.settings["rate_mbit"] * 1e6)
        queue.append((departure, size))
        return departure - now + self.settings["processing_delay_ms"] / 1000

    def valid_delivery(self, generation):
        if generation != self.generation or not self.settings["available"]:
            self.counters["outage_drops"] += 1
            return False
        return True

    def snapshot(self, now):
        return {
            **self.settings,
            **self.counters,
            "queued_bytes": sum(
                size
                for direction in self.queues
                for _, size in self.clean(direction, now)
            ),
        }
