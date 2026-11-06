import json
import time


class EventLog:
    def __init__(self, path, source=None):
        self.file = path.open("x")
        self.source = source or path.stem
        self.sequence = 0

    def emit(self, event, **fields):
        available = time.monotonic_ns()
        occurred = fields.pop("occurred_ns", fields.get("received_ns", available))
        if occurred > available:
            raise ValueError("observation cannot precede occurrence")
        reserved = {
            "schema_version",
            "source",
            "record_id",
            "time_ns",
            "available_ns",
            "algorithm_available_ns",
        }
        if reserved.intersection(fields):
            raise ValueError("reserved event field")
        forbidden = {"material", "secret", "plaintext", "ciphertext"}
        if forbidden.intersection(fields):
            raise ValueError("secret field in telemetry")
        self.sequence += 1
        record = {
            "schema_version": 1,
            "source": self.source,
            "record_id": self.sequence,
            "event": event,
            "time_ns": available,
            "occurred_ns": occurred,
            "available_ns": available,
            "algorithm_available_ns": available if self.source == "sender" else None,
            **fields,
        }
        self.file.write(json.dumps(record) + "\n")
        self.file.flush()

    def close(self):
        self.file.close()
