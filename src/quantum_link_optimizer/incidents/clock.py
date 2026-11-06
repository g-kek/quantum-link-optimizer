import math

STATES = {"locked", "holdover", "freerun", "relocking"}
DEFAULTS = {
    "state": "locked",
    "offset_ns": 0,
    "drift_ppb": 0,
    "uncertainty_ns": 50,
    "uncertainty_growth_ns_per_s": 0,
    "measurement_bias_ns": 0,
    "budget_ns": 1000,
    "source": "gm-1",
}


def validate(settings):
    if set(settings) - DEFAULTS.keys():
        raise ValueError("unknown clock setting")
    for name, value in settings.items():
        if name == "state":
            if value not in STATES:
                raise ValueError("invalid boundary clock state")
        elif name == "source":
            if not isinstance(value, str) or not 1 <= len(value) <= 128:
                raise ValueError("invalid clock source")
        elif type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError(f"invalid clock {name}")
        elif (
            name in {"uncertainty_ns", "uncertainty_growth_ns_per_s", "budget_ns"}
            and value < 0
        ):
            raise ValueError("negative clock uncertainty or budget")
    if settings.get("budget_ns", 1) == 0:
        raise ValueError("clock budget must be positive")


class BoundaryClock:
    def __init__(self, now=0, settings=None):
        self.settings = dict(DEFAULTS)
        self.anchor = now
        self.source_changes = 0
        self.update(settings or {}, now)

    def values(self, now):
        elapsed = max(0, now - self.anchor)
        return (
            self.settings["offset_ns"] + elapsed * self.settings["drift_ppb"],
            self.settings["uncertainty_ns"]
            + elapsed * self.settings["uncertainty_growth_ns_per_s"],
        )

    def update(self, settings, now):
        validate(settings)
        offset, uncertainty = self.values(now)
        self.settings.update(offset_ns=offset, uncertainty_ns=uncertainty)
        self.anchor = now
        if settings.get("source", self.settings["source"]) != self.settings["source"]:
            self.source_changes += 1
        self.settings.update(settings)

    def report(self, now):
        offset, uncertainty = self.values(now)
        estimate = offset + self.settings["measurement_bias_ns"]
        return {
            "state": self.settings["state"],
            "offset_estimate_ns": estimate,
            "uncertainty_ns": uncertainty,
            "source_changes": self.source_changes,
            "reported_usable": self.settings["state"] in {"locked", "holdover"}
            and abs(estimate) + uncertainty <= self.settings["budget_ns"],
        }

    def truth(self, now):
        offset, _ = self.values(now)
        return {
            "state": self.settings["state"],
            "sync_usable": self.settings["state"] in {"locked", "holdover"}
            and abs(offset) <= self.settings["budget_ns"],
            "actual_offset_ns": offset,
            "within_budget": abs(offset) <= self.settings["budget_ns"],
            "budget_ns": self.settings["budget_ns"],
        }
