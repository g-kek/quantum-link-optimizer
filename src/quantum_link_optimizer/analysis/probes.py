"""Ping probes with optional size, probe type, target and link load."""

import csv
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

REQUIRED = ("sample", "timestamp", "path", "status", "rtt_ms", "source", "provenance")
OPTIONAL = ("packet_bytes", "probe", "target", "load_percent")
SOURCES = {"real_telemetry", "ripe_atlas", "synthetic_example"}
PROBE_TYPES = {"icmp", "udp", "tcp", "stamp"}
PATHS = {"0": "A", "1": "B", "A": "A", "B": "B"}


@dataclass(frozen=True)
class Probe:
    sample: int
    time_s: float
    path: str
    status: str
    rtt_ms: float | None
    source: str
    packet_bytes: int | None = None
    probe: str = "icmp"
    target: str = "endpoint"
    load_percent: float | None = None


def parse_row(row, line):
    def fail(message):
        raise ValueError(f"CSV line {line}: {message}")

    try:
        sample = int(row["sample"])
    except ValueError:
        fail("sample must be an integer")

    if sample < 0:
        fail("sample must be nonnegative")

    try:
        timestamp = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
    except ValueError:
        fail("timestamp must be ISO 8601")

    if timestamp.tzinfo is None:
        fail("timestamp must include a timezone")

    path = PATHS.get(row["path"])

    if path is None:
        fail("path must be 0, 1, A or B")

    status = row["status"]

    if status not in ("ok", "timeout", "missing"):
        fail("status must be ok, timeout or missing")

    rtt = float(row["rtt_ms"]) if row["rtt_ms"] else None

    if (status == "ok") != (rtt is not None):
        fail("only status ok has an RTT value")

    if rtt is not None and (not math.isfinite(rtt) or rtt <= 0):
        fail("RTT must be finite and positive")

    if row["source"] not in SOURCES:
        fail(f"source must be one of {sorted(SOURCES)}")

    if not row["provenance"].strip():
        fail("provenance must describe how the value was measured")

    extra = {}
    size = row.get("packet_bytes") or ""

    if size:
        if not size.isdigit() or int(size) <= 0:
            fail("packet_bytes must be a positive integer")

        extra["packet_bytes"] = int(size)

    probe = (row.get("probe") or "icmp").lower()

    if probe not in PROBE_TYPES:
        fail(f"probe must be one of {sorted(PROBE_TYPES)}")

    extra["probe"] = probe
    extra["target"] = (row.get("target") or "endpoint").strip()
    load = row.get("load_percent") or ""

    if load:
        value = float(load)

        if not math.isfinite(value) or not 0 <= value <= 100:
            fail("load_percent must be between 0 and 100")

        extra["load_percent"] = value

    return Probe(
        sample, timestamp.timestamp(), path, status, rtt, row["source"], **extra
    )


def read_probes(path: Path) -> list[Probe]:
    with path.open(newline="", encoding="utf-8") as stream:
        reader = csv.DictReader(stream)
        columns = tuple(reader.fieldnames or ())

        if columns[: len(REQUIRED)] != REQUIRED:
            raise ValueError(f"CSV must start with columns {REQUIRED}")

        unknown = set(columns[len(REQUIRED) :]) - set(OPTIONAL)

        if unknown:
            raise ValueError(f"Unknown CSV columns: {sorted(unknown)}")

        probes = []
        seen = set()

        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"CSV line {reader.line_num}: wrong number of fields")

            probe = parse_row(row, reader.line_num)
            key = (
                probe.sample,
                probe.path,
                probe.target,
                probe.probe,
                probe.packet_bytes,
            )

            if key in seen:
                raise ValueError(f"CSV line {reader.line_num}: duplicate probe")

            seen.add(key)
            probes.append(probe)

    if not probes:
        raise ValueError("CSV has no probes")

    return sorted(probes, key=lambda item: (item.time_s, item.sample, item.path))


def write_probes(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(REQUIRED + OPTIONAL)

        for probe, provenance in rows:
            timestamp = datetime.fromtimestamp(probe.time_s, UTC).isoformat()
            writer.writerow(
                [
                    probe.sample,
                    timestamp,
                    probe.path,
                    probe.status,
                    "" if probe.rtt_ms is None else f"{probe.rtt_ms:.3f}",
                    probe.source,
                    provenance,
                    probe.packet_bytes or "",
                    probe.probe,
                    probe.target,
                    "" if probe.load_percent is None else f"{probe.load_percent:.1f}",
                ]
            )
