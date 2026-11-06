import math
import statistics
from array import array

from .features import FEATURE_NAMES, FEATURE_SCHEMA_VERSION


def fit(rows):
    columns = {name: array("d") for name in FEATURE_NAMES}
    episodes = set()
    count = 0
    for row in rows:
        if row["split"] != "train":
            continue
        count += 1
        episodes.add(row["episode_id"])
        for name in FEATURE_NAMES:
            value = row["features"][name]
            if value is not None:
                if not math.isfinite(value):
                    raise ValueError("nonfinite feature")
                columns[name].append(value)
    if not count:
        raise ValueError("preprocessing requires training episodes")
    stats = {}
    for name, values in columns.items():
        impute = statistics.median(values) if values else 0
        mean = statistics.mean(values) if values else 0
        variance = statistics.pvariance(values) if values else 0
        missing = count - len(values)
        combined_mean = (mean * len(values) + impute * missing) / count
        combined_variance = (
            variance * len(values)
            + (mean - impute) ** 2 * len(values) * missing / count
        ) / count
        stats[name] = {
            "impute": impute,
            "mean": combined_mean,
            "scale": math.sqrt(combined_variance) or 1,
        }
    return {
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "fit_split": "train",
        "fit_episode_ids": sorted(episodes),
        "features": stats,
    }


def transform(features, stats):
    if stats.get("feature_schema_version") != FEATURE_SCHEMA_VERSION:
        raise ValueError("preprocessing feature schema mismatch")
    result = {}
    for name, parameters in stats["features"].items():
        value = features[name]
        result[name + "_missing"] = int(value is None)
        value = parameters["impute"] if value is None else value
        result[name] = (value - parameters["mean"]) / parameters["scale"]
    return result
