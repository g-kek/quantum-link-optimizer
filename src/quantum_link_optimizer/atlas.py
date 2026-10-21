"""Single-path local RIPE Atlas benchmark; no downloads or duplicate channels."""

import csv
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from quantum_link_optimizer.observations import COLUMNS, read_observations
from quantum_link_optimizer.warning import dump_json


def atlas_benchmark(input_path: Path, output: Path):
    records = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(records, list) or len(records) < 10:
        raise ValueError(
            "Expected a local Atlas JSON list with at least 10 observations"
        )
    identities = {(r["msm_id"], r["prb_id"], r.get("dst_addr")) for r in records}
    if len(identities) != 1:
        raise ValueError("Benchmark supports exactly one measurement/probe/destination")
    records = sorted(records, key=lambda r: r["timestamp"])
    digest = hashlib.sha256(input_path.read_bytes()).hexdigest()
    output.mkdir(parents=True, exist_ok=True)
    csv_path = output / "atlas-telemetry.csv"
    lost_packets, packets = 0, 0
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(COLUMNS)
        for t, row in enumerate(records):
            results = row.get("result", [])
            rtts = [
                p["rtt"]
                for p in results
                if isinstance(p.get("rtt"), (int, float))
                and np.isfinite(p["rtt"])
                and p["rtt"] > 0
            ]
            packets += len(results)
            lost_packets += len(results) - len(rtts)
            status = "ok" if rtts else ("timeout" if results else "missing")
            writer.writerow(
                [
                    t,
                    datetime.fromtimestamp(row["timestamp"], UTC).isoformat(),
                    0,
                    status,
                    float(np.median(rtts)) if rtts else "",
                    "ripe_atlas",
                    f"msm:{row['msm_id']};probe:{row['prb_id']};raw_sha256:{digest};stat:median_successful_packets",
                ]
            )
    observations = read_observations(csv_path)
    values = np.array(
        [o.rtt[0] if o.rtt[0] is not None else np.nan for o in observations]
    )
    train_end, test_start = int(len(values) * 0.6), int(len(values) * 0.8)
    train = values[:train_end]
    train = train[np.isfinite(train)]
    if not len(train):
        raise ValueError("No training observations")
    origins = np.arange(test_start, len(values))
    valid = np.isfinite(values[origins]) & np.isfinite(values[origins - 1])
    actual, previous = values[origins[valid]], values[origins[valid] - 1]
    frequency = float(np.mean(train > 100))
    event_count = int(np.sum(actual > 100))
    gaps = np.diff([o.time_seconds for o in observations])
    report = {
        "source": "ripe_atlas",
        "raw_sha256": digest,
        "identity": list(next(iter(identities))),
        "paths": 1,
        "not_target_channels": True,
        "no_resampling": True,
        "observations": len(values),
        "sample_interval_seconds_median": float(np.median(gaps)),
        "sample_interval_seconds_min": float(np.min(gaps)),
        "sample_interval_seconds_max": float(np.max(gaps)),
        "packet_results": packets,
        "packet_results_without_valid_rtt": lost_packets,
        "aggregation": "Successful-packet median; failed packets counted separately",
        "test_origins": len(origins),
        "evaluated_origins": int(valid.sum()),
        "test_missing_or_timeout_origins": int(np.sum(~np.isfinite(values[origins]))),
        "train_frequency_sla_breach": frequency,
        "test_breaches": event_count,
        "no_test_events": event_count == 0,
        "warning_recall": None,
        "warning_note": "No warning matching; single-path baselines only",
        "persistence_mae_ms": float(np.mean(np.abs(actual - previous)))
        if len(actual)
        else None,
        "persistence_brier": float(
            np.mean(((previous > 100) - (actual > 100).astype(float)) ** 2)
        )
        if len(actual)
        else None,
        "train_frequency_brier": float(np.mean((frequency - (actual > 100)) ** 2))
        if len(actual)
        else None,
        "test_mean_rtt_ms": float(actual.mean()) if len(actual) else None,
        "test_p95_rtt_ms": float(np.quantile(actual, 0.95)) if len(actual) else None,
        "path_selection_comparison": "unavailable: only one real path",
    }
    dump_json(output / "atlas-report.json", report)
    return report
