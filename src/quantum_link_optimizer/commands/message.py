import hashlib
import math
import time
import uuid


def create(value, sequence, deadline_s=1, command_id=None):
    if not math.isfinite(deadline_s) or deadline_s <= 0:
        raise ValueError("deadline must be positive and finite")
    body = {
        "id": command_id or uuid.uuid4().hex,
        "sequence": sequence,
        "value": value,
        "deadline_ns": time.monotonic_ns() + int(deadline_s * 1e9),
    }
    validate(body)
    return body


def validate(body):
    if set(body) != {"id", "sequence", "value", "deadline_ns"}:
        raise ValueError("invalid command fields")
    if not isinstance(body["id"], str) or not 1 <= len(body["id"]) <= 128:
        raise ValueError("invalid command ID")
    for field in ("sequence", "value", "deadline_ns"):
        if type(body[field]) is not int or not 0 <= body[field] < 2**63:
            raise ValueError(f"invalid {field}")


def fingerprint(body):
    # Keep the persisted digest compatible with earlier receiver databases.
    import json

    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
