import hashlib
import json
import math
from copy import deepcopy

from quantum_link_optimizer.kms.model import Settings

SPLITS = {"train", "validation", "final_test"}
PROFILES = {
    "recoverable-700": ({"A": 700, "B": 60}, []),
    "swap-700": ({"A": 60, "B": 700}, [(0.5, {"A": 700, "B": 60})]),
    "common-700": ({"A": 700, "B": 700}, []),
    "reverse-700": ({"A": 700, "B": 60}, []),
    "slow-kms": ({"A": 60, "B": 60}, []),
    "loss-a": ({"A": 60, "B": 60}, []),
}


def directions(rtt):
    return {direction: {"delay_ms": rtt / 2} for direction in ("forward", "reverse")}


def episodes(plan):
    validate_plan(plan)
    result = []
    for split, split_config in plan["splits"].items():
        for seed in split_config["seeds"]:
            for profile in plan["profiles"]:
                for interval in split_config["intervals_s"]:
                    for fixed_path in ("A", "B"):
                        config = deepcopy(plan["base"])
                        config["samples"] = math.ceil(plan["duration_s"] / interval)
                        config.update(
                            split=split,
                            seed=seed,
                            profile=profile,
                            interval_s=interval,
                            fixed_path=fixed_path,
                        )
                        initial, changes = PROFILES[profile]
                        config["network"] = {
                            path: directions(rtt) for path, rtt in initial.items()
                        }
                        config["network_events"] = [
                            {
                                "sample": int(config["samples"] * fraction),
                                "paths": {
                                    path: directions(rtt) for path, rtt in state.items()
                                },
                            }
                            for fraction, state in changes
                        ]
                        if profile == "reverse-700":
                            config["network"]["A"] = {
                                "forward": {"delay_ms": 30},
                                "reverse": {"delay_ms": 670},
                            }
                        if profile == "slow-kms":
                            config["kms"]["delay_s"] = 0.15
                        if profile == "loss-a":
                            config["network"]["A"]["forward"]["loss_pct"] = 10
                        fingerprint = hashlib.sha256(
                            json.dumps(config, sort_keys=True).encode()
                        ).hexdigest()[:12]
                        config["episode_id"] = f"{split}-{profile}-{fingerprint}"
                        result.append(config)
    return result


def validate_plan(plan):
    if plan.get("schema_version") != 1:
        raise ValueError("unsupported plan schema")
    if set(plan["splits"]) != SPLITS:
        raise ValueError("plan requires train, validation and final_test")
    used_seeds = set()
    for settings in plan["splits"].values():
        seeds = settings["seeds"]
        if not seeds or len(set(seeds)) != len(seeds) or used_seeds.intersection(seeds):
            raise ValueError("seeds must be unique across splits")
        if any(type(seed) is not int or not 0 <= seed < 2**32 for seed in seeds):
            raise ValueError("invalid seed")
        used_seeds.update(seeds)
        if len(set(settings["intervals_s"])) != len(settings["intervals_s"]):
            raise ValueError("duplicate intervals")
        if not settings["intervals_s"] or any(
            not math.isfinite(i) or i <= 0 for i in settings["intervals_s"]
        ):
            raise ValueError("invalid intervals")
    if not plan["profiles"] or set(plan["profiles"]) - PROFILES.keys():
        raise ValueError("unknown profile")
    if len(set(plan["profiles"])) != len(plan["profiles"]):
        raise ValueError("duplicate profile")
    if not math.isfinite(plan["duration_s"]) or plan["duration_s"] <= 0:
        raise ValueError("invalid duration_s")
    base = plan["base"]
    for name in ("command_every", "queue_limit", "probe_limit", "rotate_after"):
        if type(base[name]) is not int or base[name] <= 0:
            raise ValueError(f"invalid {name}")
    for name in ("probe_timeout_s", "command_deadline_s", "target_ms", "max_age_ms"):
        if not math.isfinite(base[name]) or base[name] <= 0:
            raise ValueError(f"invalid {name}")
    if base["probe_timeout_s"] < 1:
        raise ValueError("probe timeout must preserve observations around 700 ms")
    if base["probe_timeout_s"] * 1000 < base["target_ms"]:
        raise ValueError("probe timeout must be at least target_ms")
    Settings(**base["kms"])
