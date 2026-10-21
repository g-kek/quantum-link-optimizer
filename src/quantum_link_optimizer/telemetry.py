"""Seeded two-channel simulator and strict CSV schema."""

import csv
from pathlib import Path

import numpy as np

COLUMNS = ("sample", "time_s", "channel_0_rtt_ms", "channel_1_rtt_ms", "source")
SOURCE = "synthetic_uncalibrated"


def generate_telemetry(samples: int = 4000, seed: int = 42) -> np.ndarray:
    if samples < 100:
        raise ValueError("At least 100 samples are required")
    rng = np.random.default_rng(seed)
    values = np.empty((samples, 2), dtype=np.float64)
    drift = np.zeros(2)
    congestion = np.zeros(2)
    incident = np.zeros(2, dtype=bool)
    shared = 0.0
    common_incident = False
    for index in range(samples):
        shared = 0.96 * shared + rng.normal(0, 0.65)
        common_incident = rng.random() < (0.94 if common_incident else 0.012)
        incident = rng.random(2) < np.where(incident, 0.96, 0.018)
        drift = 0.985 * drift + rng.normal(0, 0.65, 2)
        load = 27 * incident + 24 * common_incident
        congestion = 0.84 * congestion + 0.16 * load
        values[index] = np.maximum(
            1.0,
            np.array([66.0, 73.0])
            + shared
            + drift
            + congestion
            + rng.normal(0, 1.8, 2),
        )
    return values


def validate_telemetry(values: np.ndarray) -> None:
    if values.ndim != 2 or values.shape[1] != 2 or len(values) < 100:
        raise ValueError("Expected at least 100 rows and exactly two RTT columns")
    if not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError("RTT must be finite and positive")


def write_telemetry(path: Path, values: np.ndarray) -> None:
    validate_telemetry(values)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(COLUMNS)
        for index, row in enumerate(values):
            writer.writerow((index, index, *row.tolist(), SOURCE))


def read_telemetry(path: Path) -> np.ndarray:
    values = []
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if tuple(reader.fieldnames or ()) != COLUMNS:
            raise ValueError(f"Required CSV columns: {COLUMNS}")
        for index, row in enumerate(reader):
            if int(row["sample"]) != index or float(row["time_s"]) != index:
                raise ValueError("Samples must be contiguous at one-second intervals")
            if row["source"] != SOURCE:
                raise ValueError("This reader only accepts labeled synthetic data")
            values.append([float(row[name]) for name in COLUMNS[2:4]])
    result = np.asarray(values, dtype=np.float64)
    validate_telemetry(result)
    return result
