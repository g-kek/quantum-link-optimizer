"""Lifecycle events distinguish observed recovery from unavailable evidence."""

from .rules import assess

AGE_FIELDS = {
    "kms": "kms_observation_age_ms",
    "receiver": "receiver_observation_age_ms",
    "device": "device_observation_age_ms",
    "clock": "bc_observation_age_ms",
    "sender_queue": "sender_queue_observation_age_ms",
    "key": "key_observation_age_ms",
}


class IncidentTracker:
    def __init__(self, emit, max_age_ms=2000):
        self.emit = emit
        self.max_age_ms = max_age_ms
        self.active = {}
        self.sequence = 0
        self.last_ns = 0

    def available(self, finding, snapshot):
        if finding["state"] == "telemetry_missing":
            return all(
                row.get("observation_age_ms") is not None
                and 0 <= row["observation_age_ms"] <= self.max_age_ms
                for row in snapshot.values()
            )

        for item in finding["measurements"]:
            source = item["source"]

            if source == "sender" or source.startswith("active_wait/"):
                continue

            prefix, _, path = source.partition("/")
            field = AGE_FIELDS.get(prefix, "observation_age_ms")
            row = snapshot.get(path or prefix, {})
            age = row.get(field)

            if age is None or not 0 <= age <= self.max_age_ms:
                return False

        return True

    def update(self, snapshot, now_ns, **context):
        if now_ns < self.last_ns:
            raise ValueError("incident clock moved backwards")

        self.last_ns = now_ns
        findings = assess(
            snapshot, now_ns=now_ns, max_age_ms=self.max_age_ms, **context
        )
        current = {}

        for finding in findings:
            if finding["state"] == "no_anomaly_observed":
                continue

            key = finding["state"], finding["scope"]
            previous = self.active.get(key)

            if previous is None:
                self.sequence += 1
                finding.update(
                    incident_id=f"incident-{self.sequence}", lifecycle="open"
                )
                self.emit("incident_opened", **finding)
            else:
                finding.update(
                    incident_id=previous["incident_id"],
                    first_observed_ns=previous["first_observed_ns"],
                    detected_ns=previous["detected_ns"],
                    lifecycle="open",
                )

                if (
                    previous["lifecycle"] == "unobserved"
                    or now_ns - previous.get("last_emitted_ns", 0) >= 1_000_000_000
                ):
                    self.emit("incident_updated", **finding)
                    finding["last_emitted_ns"] = now_ns
                else:
                    finding["last_emitted_ns"] = previous.get("last_emitted_ns", now_ns)

            finding.setdefault("last_emitted_ns", now_ns)
            current[key] = finding

        for key, old in self.active.items():
            if key in current:
                continue

            if not self.available(old, snapshot):
                retained = {
                    **old,
                    "lifecycle": "unobserved",
                    "status": "insufficient_evidence",
                }
                current[key] = retained

                if old["lifecycle"] != "unobserved":
                    self.emit(
                        "incident_unobserved", **retained, unavailable_since_ns=now_ns
                    )
            else:
                self.emit(
                    "incident_resolved",
                    **{**old, "lifecycle": "resolved"},
                    resolved_ns=now_ns,
                    resolution_reason="condition_no_longer_observed",
                )

        self.active = current
        return list(current.values()) or findings
