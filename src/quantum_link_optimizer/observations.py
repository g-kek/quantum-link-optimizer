"""External telemetry schema; never converts real observations into synthetic data."""

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np

COLUMNS = ("sample", "timestamp", "path", "status", "rtt_ms", "source", "provenance")


@dataclass(frozen=True)
class Observation:
    sample: int
    timestamp: str
    time_seconds: float
    rtt: tuple[float | None, float | None]
    status: tuple[str, str]
    source: str
    provenance: tuple[str | None, str | None]


def read_observations(path: Path) -> list[Observation]:
    groups = {}
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        if tuple(reader.fieldnames or ()) != COLUMNS:
            raise ValueError(f"External telemetry columns must be {COLUMNS}")
        for row in reader:
            sample, channel = int(row["sample"]), int(row["path"])
            if sample < 0 or channel not in (0, 1):
                raise ValueError("Invalid sample/path")
            timestamp = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                raise ValueError("Timestamp must include timezone")
            status = row["status"]
            if status not in ("ok", "timeout", "missing"):
                raise ValueError("Unknown observation status")
            rtt = float(row["rtt_ms"]) if row["rtt_ms"] else None
            if (status == "ok") != (rtt is not None):
                raise ValueError("Only status ok must have RTT; others must be empty")
            if rtt is not None and (not np.isfinite(rtt) or rtt <= 0):
                raise ValueError("RTT must be finite and positive")
            if (
                row["source"] not in ("real_telemetry", "ripe_atlas")
                or not row["provenance"].strip()
            ):
                raise ValueError("Real source and nonempty provenance required")
            entry = groups.setdefault(sample, {})
            if channel in entry:
                raise ValueError("Duplicate path at sample")
            entry[channel] = (row, timestamp.timestamp(), rtt)
    observations = []
    for sample, group in groups.items():
        row, seconds, _ = next(iter(group.values()))
        if any(
            other[0]["timestamp"] != row["timestamp"]
            or other[0]["source"] != row["source"]
            for other in group.values()
        ):
            raise ValueError("Paths at sample must have identical timestamp and source")
        if observations and (
            sample != observations[-1].sample + 1
            or seconds <= observations[-1].time_seconds
        ):
            raise ValueError(
                "Samples must be contiguous and timestamps strictly increasing"
            )
        observations.append(
            Observation(
                sample,
                row["timestamp"],
                seconds,
                tuple(group[c][2] if c in group else None for c in (0, 1)),
                tuple(
                    group[c][0]["status"] if c in group else "missing" for c in (0, 1)
                ),
                row["source"],
                tuple(
                    group[c][0]["provenance"] if c in group else None for c in (0, 1)
                ),
            )
        )
    if not observations:
        raise ValueError("Telemetry is empty")
    return observations
