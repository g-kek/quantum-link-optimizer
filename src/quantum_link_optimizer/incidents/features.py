STATES = ("locked", "holdover", "freerun", "relocking")
DEVICE_FIELDS = {
    "device_available": "available",
    "device_rate_mbit": "rate_mbit",
    "device_queued_bytes": "queued_bytes",
    "device_error_drops": "error_drops",
    "device_queue_drops": "queue_drops",
    "device_outage_drops": "outage_drops",
    "device_temperature_c": "temperature_c",
}
CLOCK_FIELDS = {
    "bc_offset_estimate_ns": "offset_estimate_ns",
    "bc_uncertainty_ns": "uncertainty_ns",
    "bc_reported_usable": "reported_usable",
    "bc_source_changes": "source_changes",
}
FEATURE_NAMES = (
    *DEVICE_FIELDS,
    *CLOCK_FIELDS,
    *(f"bc_{state}" for state in STATES),
    "device_observation_age_ms",
    "device_stale",
    "device_missing",
    "bc_observation_age_ms",
    "bc_stale",
    "bc_missing",
)


class ComponentHistory:
    def __init__(self, max_age_ms):
        self.max_age_ms = max_age_ms
        self.observations = {}

    def observe(self, event):
        if event["event"] != "component_observation":
            return
        if event["generated_ns"] > event["algorithm_available_ns"]:
            raise ValueError("component measurement from the future")
        for name in ("device", "clock"):
            if event.get(name) is None:
                continue
            key = event["path"], name
            previous = self.observations.get(key)
            if previous is None or event["generated_ns"] >= previous["generated_ns"]:
                self.observations[key] = event

    def at(self, path, decision_ns):
        result = dict.fromkeys(FEATURE_NAMES)
        used = []
        for name, prefix, fields in (
            ("device", "device", DEVICE_FIELDS),
            ("clock", "bc", CLOCK_FIELDS),
        ):
            event = self.observations.get((path, name))
            if event and event["algorithm_available_ns"] > decision_ns:
                raise ValueError("component report is not available at decision time")
            age = (decision_ns - event["generated_ns"]) / 1e6 if event else None
            stale = age is not None and age > self.max_age_ms
            result[prefix + "_observation_age_ms"] = age
            result[prefix + "_missing"] = int(event is None)
            result[prefix + "_stale"] = int(stale)
            if event:
                used.append(event["algorithm_available_ns"])
            if event is None or stale:
                continue
            for feature, field in fields.items():
                result[feature] = event[name][field]
            if name == "clock":
                result.update(
                    {
                        f"bc_{state}": int(event[name]["state"] == state)
                        for state in STATES
                    }
                )
        return result, max(used, default=None)
