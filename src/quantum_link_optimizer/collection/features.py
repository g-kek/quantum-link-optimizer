from collections import deque

from quantum_link_optimizer.incidents.features import (
    FEATURE_NAMES as COMPONENT_FEATURES,
)
from quantum_link_optimizer.incidents.features import ComponentHistory

FEATURE_SCHEMA_VERSION = 2

FEATURE_NAMES = (
    "path_is_b",
    "history_count",
    "last_rtt_ms",
    "ewma_rtt_ms",
    "timeout_fraction",
    "missing_fraction",
    "observation_age_ms",
    "stale",
    "no_history",
    "last_key_wait_ms",
    "key_observation_age_ms",
    "sender_queue_depth",
    *COMPONENT_FEATURES,
)


class History:
    def __init__(self, max_age_ms, window=32):
        self.max_age_ms = max_age_ms
        self.samples = {path: deque(maxlen=window) for path in ("A", "B")}
        self.keys = {}
        self.queues = {}
        self.restarts = {}
        self.components = ComponentHistory(max_age_ms)

    def restart(self, path, available_ns):
        """Forget the level before a step, EWMA starts again from here."""
        self.restarts[path] = available_ns

    def observe(self, event):
        path = event.get("path")
        if path not in self.samples:
            return
        self.components.observe(event)
        if event["event"] == "probe_result":
            self.samples[path].append(event)
        elif event["event"] == "session_ready":
            self.keys[path] = event
        elif event["event"] == "sender_queue_sample":
            self.queues[path] = event

    def at(self, path, decision_ns):
        history = self.samples[path]
        fresh = [
            event
            for event in history
            if (decision_ns - event["algorithm_available_ns"]) / 1e6 <= self.max_age_ms
        ]
        latest = history[-1] if history else None
        age = (decision_ns - latest["algorithm_available_ns"]) / 1e6 if latest else None
        since = self.restarts.get(path, 0)
        valid = [
            event["rtt_ms"]
            for event in fresh
            if event["status"] == "ok" and event["algorithm_available_ns"] >= since
        ]
        ewma = None
        for value in valid:
            ewma = value if ewma is None else 0.3 * value + 0.7 * ewma
        key = self.keys.get(path)
        queue = self.queues.get(path)
        used = fresh + ([key] if key else []) + ([queue] if queue else [])
        features = {
            "path_is_b": int(path == "B"),
            "history_count": len(fresh),
            "last_rtt_ms": fresh[-1]["rtt_ms"]
            if fresh and fresh[-1]["status"] == "ok"
            else None,
            "ewma_rtt_ms": ewma,
            "timeout_fraction": sum(event["status"] == "timeout" for event in fresh)
            / len(fresh)
            if fresh
            else None,
            "missing_fraction": sum(event["status"] == "missing" for event in fresh)
            / len(fresh)
            if fresh
            else None,
            "observation_age_ms": age,
            "stale": int(age is not None and age > self.max_age_ms),
            "no_history": int(not fresh),
            "last_key_wait_ms": key["key_wait_ns"] / 1e6 if key else None,
            "key_observation_age_ms": (decision_ns - key["algorithm_available_ns"])
            / 1e6
            if key
            else None,
            "sender_queue_depth": queue["queue_depth"] if queue else None,
        }
        latest_ns = max(
            (event["algorithm_available_ns"] for event in used), default=None
        )
        component_features, component_ns = self.components.at(path, decision_ns)
        features.update(component_features)
        latest_ns = max(
            (value for value in (latest_ns, component_ns) if value is not None),
            default=None,
        )
        if latest_ns is not None and latest_ns > decision_ns:
            raise ValueError("future observation in features")
        return features, latest_ns


def rows(config, events, truth_events=()):
    truth = {
        (event["sample"], event["path"]): event
        for event in truth_events
        if event["event"] == "incident_state"
    }
    decisions = [event for event in events if event["event"] == "decision"]
    probes = {}
    commands = {}
    for event in events:
        if event["event"] == "probe_result":
            key = event["sample"], event["path"]
            if key in probes:
                raise ValueError("duplicate probe result")
            probes[key] = event
        if event["event"] == "command_completed" and "sample" in event:
            commands[event["sample"], event["path"]] = event
    observations = sorted(
        (event for event in events if event.get("algorithm_available_ns") is not None),
        key=lambda event: (event["algorithm_available_ns"], event["record_id"]),
    )
    history = History(config["max_age_ms"])
    cursor = 0
    for decision in sorted(decisions, key=lambda event: event["available_ns"]):
        decision_ns = decision["available_ns"]
        while cursor < len(observations) and (
            observations[cursor]["algorithm_available_ns"],
            observations[cursor]["record_id"],
        ) <= (decision_ns, decision["record_id"]):
            history.observe(observations[cursor])
            cursor += 1
        for path in ("A", "B"):
            features, observed_ns = history.at(path, decision_ns)
            probe = probes.get((decision["sample"], path))
            command = commands.get((decision["sample"], path))
            rtt = probe["rtt_ms"] if probe else None
            command_ms = (
                command.get("elapsed_ns", 0) / 1e6
                if command and "elapsed_ns" in command
                else None
            )
            yield {
                "feature_schema_version": FEATURE_SCHEMA_VERSION,
                "episode_id": config["episode_id"],
                "split": config["split"],
                "sample": decision["sample"],
                "path": path,
                "decision_ns": decision_ns,
                "feature_available_ns": observed_ns,
                "features": features,
                "targets": {
                    **component_targets(truth.get((decision["sample"], path))),
                    "probe_status": probe["status"] if probe else "missing",
                    "rtt_ms": rtt,
                    "probe_within_target": (
                        rtt <= config["target_ms"]
                        if rtt is not None
                        else False
                        if probe and probe["status"] == "timeout"
                        else None
                    ),
                    "probe_available_ns": probe["available_ns"] if probe else None,
                    "command_status": command["status"] if command else None,
                    "command_elapsed_ms": command_ms,
                    "command_within_target": (
                        command["status"] == "executed"
                        and command_ms is not None
                        and command_ms <= config["target_ms"]
                    )
                    if command
                    else None,
                    "command_available_ns": command["available_ns"]
                    if command
                    else None,
                },
            }


def component_targets(event):
    return {
        "bc_state_at_decision": event["clock"].get("state") if event else None,
        "bc_sync_usable_at_decision": event["clock"].get("sync_usable")
        if event
        else None,
        "device_available_at_decision": event["device_available"] if event else None,
        "bc_actual_offset_ns_at_decision": event["clock"]["actual_offset_ns"]
        if event
        else None,
        "bc_within_budget_at_decision": event["clock"]["within_budget"]
        if event
        else None,
        "component_truth_recorded_ns": event["available_ns"] if event else None,
    }
