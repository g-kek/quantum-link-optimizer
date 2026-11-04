import asyncio

from .clock import BoundaryClock
from .device import Device
from .profiles import validate


class Runtime:
    def __init__(self, spec, samples, seed, emit, truth):
        validate(spec, samples)
        self.spec = spec
        self.emit = emit
        self.truth = truth
        self.loop = asyncio.get_running_loop()
        self.devices = self.components(
            "devices",
            "device_scope",
            lambda settings, path: Device(
                settings, seed + {"A": 0, "B": 1, "shared": 2}[path]
            ),
        )
        self.clocks = self.components(
            "clocks",
            "clock_scope",
            lambda settings, path: BoundaryClock(self.loop.time(), settings),
        )
        self.monitors = {
            path: {
                "device_enabled": True,
                "clock_enabled": True,
                "delay_s": spec.get("monitor", {}).get("delay_s", 0),
            }
            for path in ("A", "B")
        }
        self.every = spec.get("monitor", {}).get("every_samples", 1)
        for state in self.monitors.values():
            state.update(
                {
                    k: v
                    for k, v in spec.get("monitor", {}).items()
                    if k != "every_samples"
                }
            )
        self.timers = set()
        self.truth("incident_assumptions", assumptions=spec["assumptions"])

    def components(self, field, scope, create):
        initial = self.spec.get(field, {})
        if self.spec.get(scope) == "shared":
            component = create(next(iter(initial.values()), {}), "shared")
            return {"A": component, "B": component}
        return {path: create(initial.get(path, {}), path) for path in ("A", "B")}

    def tick(self, sample):
        now = self.loop.time()
        for event in self.spec.get("events", []):
            if event["sample"] != sample:
                continue
            for path, settings in event.get("devices", {}).items():
                self.devices[path].update(settings)
            for path, settings in event.get("clocks", {}).items():
                self.clocks[path].update(settings, now)
            for path, settings in event.get("monitors", {}).items():
                self.monitors[path].update(settings)
            self.truth("incident_injection", **event)
        for path in ("A", "B"):
            self.truth(
                "incident_state",
                sample=sample,
                path=path,
                device_available=self.devices[path].settings["available"],
                clock=self.clocks[path].truth(now),
            )
            if sample % self.every:
                continue
            monitor = self.monitors[path]
            payload = {
                "path": path,
                "generated_ns": int(now * 1e9),
                "device": self.devices[path].snapshot(now)
                if monitor["device_enabled"]
                else None,
                "clock": self.clocks[path].report(now)
                if monitor["clock_enabled"]
                else None,
            }
            if payload["device"] is None and payload["clock"] is None:
                continue
            if len(self.timers) >= 256:
                self.truth(
                    "monitor_drop", path=path, sample=sample, reason="pending_limit"
                )
                continue
            self.schedule(payload, monitor["delay_s"])

    def schedule(self, payload, delay):
        timer = None

        def deliver():
            self.timers.discard(timer)
            self.emit("component_observation", **payload)

        timer = self.loop.call_later(delay, deliver)
        self.timers.add(timer)

    def close(self):
        for timer in self.timers:
            timer.cancel()
        self.timers.clear()
