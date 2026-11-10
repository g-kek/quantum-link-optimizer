"""Declared laboratory conditions, never used as model input."""

from copy import deepcopy

CASES = ("normal", "swap", "common-700", "slow-kms", "key-exhaustion")


def configuration(case, seed, count=18, interval_s=0.1, max_in_flight=8):
    if case not in {*CASES, "shared-queue", "receiver-restart", "loss-a"}:
        raise ValueError("unknown laboratory case")

    config = {
        "case": case,
        "seed": seed,
        "count": count,
        "interval_s": interval_s,
        "deadline_s": 0.8,
        "target_ms": 100,
        "warmup_s": 0.25,
        "probe_interval_s": 0.05,
        "key_horizon_s": 0.25,
        "queue_limit": 64,
        "max_in_flight": max_in_flight,
        "probe_limit": 64,
        "probe_timeout_s": 1.2,
        "rotate_after": 4,
        "network": {
            "A": {"forward": {"delay_ms": 30}, "reverse": {"delay_ms": 30}},
            "B": {"forward": {"delay_ms": 45}, "reverse": {"delay_ms": 45}},
        },
        "kms": {
            "mode": "shared",
            "initial": 64,
            "capacity": 64,
            "refill_per_s": 1,
            "delay_s": 0,
            "key_ttl_s": 60,
        },
    }

    if case == "swap":
        config["network"]["A"] = {
            "forward": {"delay_ms": 80},
            "reverse": {"delay_ms": 80},
        }
        config["network"]["B"] = {
            "forward": {"delay_ms": 20},
            "reverse": {"delay_ms": 20},
        }

    if case == "common-700":
        for settings in config["network"].values():
            settings["forward"]["delay_ms"] = 350
            settings["reverse"]["delay_ms"] = 350

    if case == "slow-kms":
        config["kms"]["delay_s"] = 0.06
        config["rotate_after"] = 2

    if case == "key-exhaustion":
        config["kms"].update(initial=3, capacity=3, refill_per_s=0)
        config["rotate_after"] = 1

    if case == "loss-a":
        config["network"]["A"]["forward"]["loss_pct"] = 25

    return deepcopy(config)
