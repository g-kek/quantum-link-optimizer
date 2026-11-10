import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    mode: str = "ewma"
    max_age_ms: float = 2000
    switch_margin_ms: float = 10
    min_hold_ms: float = 500
    failure_threshold: int = 2
    require_clock: bool = False
    shared_segment: bool = False
    hedge_delay_ms: float | None = None
    extra_bytes_per_command: int = 1200
    extra_bytes_total: int = 24000
    max_attempts_per_leg: int = 2
    queue_limit: int = 64
    pacing_per_s: float = 0
    pacing_max_wait_ms: float | None = None
    max_in_flight: int = 8
    coalesce: bool = False
    kms_cooldown_ms: float = 200
    diagnostic_interval_ms: float = 50
    target_ms: float = 100

    def __post_init__(self):
        if self.mode not in {"fixed-a", "fixed-b", "last", "ewma"}:
            raise ValueError("unknown selection mode")
        for name in (
            "max_age_ms",
            "switch_margin_ms",
            "min_hold_ms",
            "pacing_per_s",
            "kms_cooldown_ms",
            "target_ms",
            "diagnostic_interval_ms",
        ):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise ValueError(f"invalid {name}")
        if not self.max_age_ms or not self.target_ms or not self.diagnostic_interval_ms:
            raise ValueError("age and target must be positive")
        if self.hedge_delay_ms is not None and (
            type(self.hedge_delay_ms) not in (float, int)
            or not math.isfinite(self.hedge_delay_ms)
            or self.hedge_delay_ms < 0
        ):
            raise ValueError("invalid hedge delay")
        for name in (
            "failure_threshold",
            "extra_bytes_per_command",
            "extra_bytes_total",
            "max_attempts_per_leg",
            "queue_limit",
            "max_in_flight",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < (0 if name.startswith("extra") else 1):
                raise ValueError(f"invalid {name}")
        if (
            type(self.require_clock) is not bool
            or type(self.shared_segment) is not bool
            or type(self.coalesce) is not bool
        ):
            raise ValueError("clock, topology and coalesce flags must be boolean")

        if self.pacing_max_wait_ms is not None and (
            type(self.pacing_max_wait_ms) not in (float, int)
            or not math.isfinite(self.pacing_max_wait_ms)
            or self.pacing_max_wait_ms < 0
        ):
            raise ValueError("invalid pacing_max_wait_ms")
