class Policy:
    def __init__(self, settings):
        self.settings = settings
        self.current = "B" if settings.mode == "fixed-b" else "A"
        self.changed_ns = None

    def eligible(self, row):
        if row["device_available"] is not None and not row["device_available"]:
            return False
        if row["consecutive_timeouts"] >= self.settings.failure_threshold:
            return False
        if row["kms_blocked"]:
            return False
        if self.settings.require_clock and row["bc_reported_usable"] is not True:
            return False
        return True

    def score(self, row):
        name = "last_rtt_ms" if self.settings.mode == "last" else "ewma_rtt_ms"
        value = row[name]
        return value + row["key_setup_ms"] if value is not None else float("inf")

    def choose(self, snapshot, now_ns):
        if self.changed_ns is None:
            self.changed_ns = now_ns
        if self.settings.mode.startswith("fixed"):
            if (
                self.settings.require_clock
                and snapshot[self.current]["bc_reported_usable"] is not True
            ):
                return None, "clock_not_ready"
            return self.current, "fixed"
        candidates = [path for path, row in snapshot.items() if self.eligible(row)]
        if not candidates:
            return None, "no_eligible_path"
        best = min(
            candidates,
            key=lambda path: (self.score(snapshot[path]), path != self.current),
        )
        if self.current not in candidates:
            self.current, self.changed_ns = best, now_ns
            return best, "emergency"
        if self.settings.shared_segment and all(
            row["ewma_rtt_ms"] is not None
            and row["ewma_rtt_ms"] > self.settings.target_ms
            for row in snapshot.values()
        ):
            return self.current, "common_slowdown"
        if best == self.current:
            return best, "retain"
        if (
            snapshot[self.current]["no_history"] or snapshot[self.current]["stale"]
        ) and not snapshot[best]["no_history"]:
            self.current, self.changed_ns = best, now_ns
            return best, "fresh_observation"
        held_ms = (now_ns - self.changed_ns) / 1e6
        if held_ms < self.settings.min_hold_ms:
            return self.current, "hold"
        if (
            self.score(snapshot[self.current]) - self.score(snapshot[best])
            <= self.settings.switch_margin_ms
        ):
            return self.current, "hysteresis"
        self.current, self.changed_ns = best, now_ns
        return best, "better_path"

    def alternative(self, primary, snapshot):
        other = "B" if primary == "A" else "A"
        if not self.eligible(snapshot[other]):
            return None, "alternative_ineligible"
        if snapshot[other]["no_history"] or snapshot[other]["stale"]:
            return None, "alternative_unobserved"
        slow = all(
            row["ewma_rtt_ms"] is not None
            and row["ewma_rtt_ms"] > self.settings.target_ms
            for row in snapshot.values()
        )
        queued = any((row["device_queued_bytes"] or 0) > 0 for row in snapshot.values())
        if slow or self.settings.shared_segment and queued:
            return None, "common_slowdown_or_queue"
        return other, "hedge"
