from . import clock, device

NAMES = {
    "equipment-gradual-a",
    "equipment-rate-a",
    "equipment-outage-a",
    "equipment-shared-outage",
    "bc-holdover-a",
    "bc-step-a",
    "bc-source-switch-a",
    "bc-monitor-gap-a",
    "bc-common-loss",
    "bc-biased-monitor-a",
}


def profile(name, samples):
    spec = {
        "device_scope": "separate",
        "clock_scope": "separate",
        "devices": {path: dict(device.DEFAULTS) for path in ("A", "B")},
        "clocks": {path: dict(clock.DEFAULTS) for path in ("A", "B")},
        "monitor": {"every_samples": 2, "delay_s": 0.15},
        "events": [],
        "assumptions": [
            {
                "id": "unvalidated-equipment-clock-v1",
                "status": "assumption",
                "description": (
                    "All numeric device, clock and monitor parameters are laboratory "
                    "hypotheses; not field measurements or standards limits."
                ),
            }
        ],
    }

    def add(fraction, **changes):
        spec["events"].append({"sample": int(samples * fraction), **changes})

    if name == "equipment-gradual-a":
        for fraction, delay, temperature in (
            (0.2, 20, 45),
            (0.4, 80, 55),
            (0.6, 160, 65),
            (0.8, 320, 75),
        ):
            add(
                fraction,
                devices={
                    "A": {"processing_delay_ms": delay, "temperature_c": temperature}
                },
            )
    elif name == "equipment-rate-a":
        add(0.25, devices={"A": {"rate_mbit": 0.002, "queue_limit_bytes": 1024}})
        add(0.75, devices={"A": {"rate_mbit": 100}})
    elif name in {"equipment-outage-a", "equipment-shared-outage"}:
        if name == "equipment-shared-outage":
            spec["device_scope"] = "shared"
        add(0.25, devices={"A": {"available": False}})
        add(0.65, devices={"A": {"available": True}})
    elif name == "bc-holdover-a":
        add(
            0.2,
            clocks={
                "A": {
                    "state": "holdover",
                    "drift_ppb": 500,
                    "uncertainty_growth_ns_per_s": 500,
                }
            },
        )
        add(0.75, clocks={"A": {"state": "relocking", "drift_ppb": 0}})
        add(
            0.85,
            clocks={
                "A": {
                    "state": "locked",
                    "offset_ns": 0,
                    "uncertainty_ns": 50,
                    "uncertainty_growth_ns_per_s": 0,
                }
            },
        )
    elif name in {"bc-step-a", "bc-biased-monitor-a"}:
        settings = {"offset_ns": 50000}
        if name == "bc-biased-monitor-a":
            settings["measurement_bias_ns"] = -50000
        add(0.25, clocks={"A": settings})
        add(0.75, clocks={"A": {"offset_ns": 0, "measurement_bias_ns": 0}})
    elif name == "bc-source-switch-a":
        add(
            0.25,
            clocks={"A": {"source": "gm-2", "state": "relocking", "offset_ns": 2000}},
        )
        add(0.5, clocks={"A": {"state": "locked", "offset_ns": 0}})
    elif name == "bc-monitor-gap-a":
        add(0.2, monitors={"A": {"clock_enabled": False}})
        add(0.3, clocks={"A": {"state": "freerun", "drift_ppb": 500}})
        add(0.8, monitors={"A": {"clock_enabled": True}})
    elif name == "bc-common-loss":
        spec["clock_scope"] = "shared"
        add(0.25, clocks={"A": {"state": "freerun", "drift_ppb": 500}})
        add(0.75, clocks={"A": {"state": "locked", "drift_ppb": 0, "offset_ns": 0}})
    else:
        raise ValueError("unknown incident profile")
    return spec


def validate(spec, samples):
    if set(spec) - {
        "device_scope",
        "clock_scope",
        "devices",
        "clocks",
        "monitor",
        "events",
        "assumptions",
    }:
        raise ValueError("unknown incidents field")
    for name in ("device_scope", "clock_scope"):
        if spec.get(name, "separate") not in {"shared", "separate"}:
            raise ValueError("invalid incident scope")
    assumptions = spec.get("assumptions", [])
    if not assumptions or any(
        item.get("status") != "assumption" or not item.get("description")
        for item in assumptions
    ):
        raise ValueError("uncalibrated model requires explicit assumptions")
    for name, checker in (("devices", device.validate), ("clocks", clock.validate)):
        for path, settings in spec.get(name, {}).items():
            if path not in {"A", "B"}:
                raise ValueError("invalid incident path")
            checker(settings)
        scope = "device_scope" if name == "devices" else "clock_scope"
        if spec.get(scope) == "shared" and len(spec.get(name, {})) == 2:
            if spec[name]["A"] != spec[name]["B"]:
                raise ValueError(
                    "shared component cannot have conflicting initial settings"
                )
    monitor(spec.get("monitor", {}), initial=True)
    for event in spec.get("events", []):
        if type(event["sample"]) is not int or not 0 <= event["sample"] < samples:
            raise ValueError("incident event outside episode")
        if set(event) - {"sample", "devices", "clocks", "monitors"}:
            raise ValueError("unknown incident event field")
        for name, checker in (
            ("devices", device.validate),
            ("clocks", clock.validate),
            ("monitors", monitor),
        ):
            for path, settings in event.get(name, {}).items():
                if path not in {"A", "B"}:
                    raise ValueError("invalid event path")
                checker(settings)
            scope = "device_scope" if name == "devices" else "clock_scope"
            values = event.get(name, {})
            if (
                name != "monitors"
                and spec.get(scope) == "shared"
                and len(values) == 2
                and values["A"] != values["B"]
            ):
                raise ValueError("conflicting event on a shared component")


def monitor(settings, initial=False):
    import math

    allowed = {"device_enabled", "clock_enabled", "delay_s"}
    if initial:
        allowed.add("every_samples")
    if set(settings) - allowed:
        raise ValueError("unknown monitor setting")
    for name, value in settings.items():
        if name.endswith("enabled") and type(value) is not bool:
            raise ValueError("monitor enabled must be boolean")
        if name == "every_samples" and (type(value) is not int or value < 1):
            raise ValueError("monitor interval must be positive")
        if name == "delay_s" and (
            type(value) not in (int, float) or not math.isfinite(value) or value < 0
        ):
            raise ValueError("invalid monitor delay")
