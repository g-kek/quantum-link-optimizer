"""Only information delivered to the sender before its decision."""

FEATURES = (
    "last_rtt_ms",
    "ewma_rtt_ms",
    "timeout_fraction",
    "missing_fraction",
    "observation_age_ms",
    "last_key_wait_ms",
    "key_observation_age_ms",
    "sender_queue_depth",
    "key_ready",
    "key_setup_ms",
    "kms_available_keys",
    "kms_demand_per_s",
    "kms_refill_per_s",
    "kms_observation_age_ms",
    "command_bytes",
)


def vector(row):
    return [row.get(name) for name in FEATURES]


def exhaustion_seconds(available, demand, refill):
    if available is None or demand is None or refill is None:
        return None

    if available <= 0:
        return 0.0

    drain = demand - refill

    if drain <= 0:
        return None

    return available / drain
