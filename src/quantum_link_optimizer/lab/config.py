import json
import math
import re


def load_config(path):
    config = json.loads(path.read_text())
    if config.get("topology") not in {"independent", "shared"}:
        raise ValueError("topology must be independent or shared")
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,30}", config.get("name", "")):
        raise ValueError("invalid scenario name")
    for key in ("duration_s", "interval_s"):
        value = config.get(key)
        if isinstance(value, bool) or not isinstance(value, (float, int)):
            raise ValueError(f"{key} must be numeric")
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{key} must be positive and finite")
    for branch in ("A", "B"):
        for direction in ("forward", "reverse"):
            validate_link(config["paths"][branch][direction])
    for direction, link in config.get("shared", {}).items():
        if direction not in {"forward", "reverse"}:
            raise ValueError("invalid shared direction")
        validate_link(link)
    for event in config.get("events", []):
        if event["path"] not in {"A", "B"}:
            raise ValueError("event path must be A or B")
        if event["action"] not in {"down", "up"}:
            raise ValueError("event action must be down or up")
        if not 0 <= event["at_s"] < config["duration_s"]:
            raise ValueError("event outside measurement window")
    for flow in config.get("background", []):
        if flow["path"] not in {"A", "B"}:
            raise ValueError("invalid background path")
        validate_link({"rate_mbit": flow["rate_mbit"]})
    if "commands" in config:
        commands = config["commands"]
        if commands["path"] not in {"A", "B"}:
            raise ValueError("invalid command path")
        if type(commands["count"]) is not int or commands["count"] <= 0:
            raise ValueError("command count must be positive")
        if not isinstance(commands["kms_config"], str):
            raise ValueError("kms_config must be a path")
        duration = commands["count"] * commands.get("interval_s", 0.02)
        if duration + commands.get("deadline_s", 1) > config["duration_s"]:
            raise ValueError("command workload exceeds measurement window")
    return config


def validate_link(link):
    allowed = {"delay_ms", "jitter_ms", "loss_pct", "reorder_pct", "rate_mbit", "limit"}
    if set(link) - allowed:
        raise ValueError("unknown link setting")
    for key, value in link.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{key} must be numeric")
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"invalid {key}")
        if key.endswith("_pct") and value > 100:
            raise ValueError(f"invalid {key}")
        if key in {"rate_mbit", "limit"} and value <= 0:
            raise ValueError(f"{key} must be positive")
        if key == "limit" and not isinstance(value, int):
            raise ValueError("limit must be an integer")
    if link.get("reorder_pct") and not link.get("delay_ms"):
        raise ValueError("reordering requires delay_ms")
