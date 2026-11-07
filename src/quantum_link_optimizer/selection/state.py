import time

from quantum_link_optimizer.collection.features import History


class Observations:
    def __init__(self, settings):
        self.history = History(settings.max_age_ms)
        self.settings = settings
        self.failures = dict.fromkeys(("A", "B"), 0)
        self.kms_blocked_until = dict.fromkeys(("A", "B"), 0)
        self.last_available_ns = 0
        self.sequence = 0
        self.setup_costs = {}
        self.acks = {}
        self.inventory = None
        self.key_waits = {path: {} for path in ("A", "B")}

    def observe(self, event, **fields):
        now = time.monotonic_ns()
        available = fields.pop("available_ns", now)
        if available > now or available < self.last_available_ns:
            raise ValueError("invalid observation availability")
        self.last_available_ns = available
        self.sequence += 1
        record = {
            **fields,
            "event": event,
            "available_ns": available,
            "algorithm_available_ns": available,
            "record_id": self.sequence,
        }
        self.history.observe(record)
        if event == "kms_observed":
            self.inventory = record

        path = fields.get("path")
        if path not in self.failures:
            return
        command = fields.get("command_id")

        if event == "key_wait_started" and command is not None:
            self.key_waits[path][command] = available

        if event in {
            "session_ready",
            "leg_completed",
            "command_completed",
            "leg_abandoned",
        }:
            self.key_waits[path].pop(command, None)

        if event == "ack_received":
            self.acks[path] = record
        if event == "probe_result":
            if fields["status"] == "ok":
                self.failures[path] = 0
            elif fields["status"] == "timeout":
                self.failures[path] += 1
        if event == "session_ready":
            if fields["phase"] != "steady":
                self.setup_costs[path] = fields["key_wait_ns"] / 1e6
            self.kms_blocked_until[path] = 0
        if (
            event in {"command_completed", "leg_completed"}
            and fields.get("status") == "kms_error"
        ):
            self.kms_blocked_until[path] = available + int(
                self.settings.kms_cooldown_ms * 1e6
            )

    def snapshot(self, now_ns, senders):
        result = {}
        for path in ("A", "B"):
            features, latest = self.history.at(path, now_ns)
            session = senders[path].session
            phase = session.phase()
            observed_costs = list(self.setup_costs.values())
            ack = self.acks.get(path)
            receiver = {}
            for name in (
                "receiver_queue_wait_ns",
                "receiver_key_wait_ns",
                "receiver_processing_ns",
            ):
                receiver[name] = (
                    ack.get(name)
                    if ack
                    and now_ns - ack["available_ns"] <= self.settings.max_age_ms * 1e6
                    else None
                )
            inventory = self.inventory
            kms_age = (now_ns - inventory["available_ns"]) / 1e6 if inventory else None
            kms_fresh = kms_age is not None and 0 <= kms_age <= self.settings.max_age_ms
            pool = (
                "shared" if inventory and "shared" in inventory["available"] else path
            )
            oldest_wait = min(self.key_waits[path].values(), default=now_ns)

            result[path] = {
                "kms_pool": pool,
                "kms_available_keys": inventory["available"].get(pool)
                if kms_fresh
                else None,
                "kms_observation_age_ms": kms_age,
                "kms_request_ms": inventory.get("request_elapsed_ns", 0) / 1e6
                if kms_fresh
                else None,
                "active_key_wait_ms": (now_ns - oldest_wait) / 1e6,
                **receiver,
                "sender_queue_observation_age_ms": (
                    now_ns - self.history.queues[path]["algorithm_available_ns"]
                )
                / 1e6
                if path in self.history.queues
                else None,
                "receiver_observation_age_ms": (now_ns - ack["available_ns"]) / 1e6
                if ack
                else None,
                **features,
                "feature_available_ns": latest,
                "consecutive_timeouts": self.failures[path],
                "kms_blocked": now_ns < self.kms_blocked_until[path],
                "key_ready": phase == "steady",
                "key_setup_ms": 0
                if phase == "steady"
                else max(observed_costs, default=0),
            }
        return result
