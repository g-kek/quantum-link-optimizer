"""Recompute planning evidence from saved CSVs, without production metric helpers.

Run from the repository root. This is an audit of existing synthetic runs,
not a network simulation, an independent holdout, or a live-agent benchmark.
"""

import argparse
import csv
import hashlib
import json
import math
import statistics
from pathlib import Path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def quantile(values, fraction):
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    left, right = math.floor(position), math.ceil(position)
    return ordered[left] + (ordered[right] - ordered[left]) * (position - left)


def read_csv(path):
    with path.open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def check_run(directory):
    report_path = directory / "risk-report.json"
    report = json.loads(report_path.read_text())
    telemetry_path = directory / "telemetry.csv"
    decisions_path = directory / "risk-decisions.csv"
    raw = read_csv(telemetry_path)
    assert all(row["source"] == "synthetic_uncalibrated" for row in raw)
    telemetry = {
        int(row["sample"]): [
            float(row["channel_0_rtt_ms"]),
            float(row["channel_1_rtt_ms"]),
        ]
        for row in raw
    }
    assert len(telemetry) == len(raw), "Duplicate telemetry sample"
    decisions = read_csv(decisions_path)
    origins = [int(row["sample"]) for row in decisions]
    assert origins == list(
        range(report["test_first_origin"], report["test_last_origin"] + 1)
    )
    metrics = {}
    cost = report["configuration"]["switching_cost_ms"]
    for name, saved in report["policies"].items():
        choices = [int(row[name]) for row in decisions]
        assert all(choice in (0, 1) for choice in choices)
        values = [telemetry[t][p] for t, p in zip(origins, choices, strict=True)]
        switches = [False] + [
            a != b for a, b in zip(choices[:-1], choices[1:], strict=True)
        ]
        effective = [v + cost * s for v, s in zip(values, switches, strict=True)]
        calculated = {
            "samples": len(values),
            "mean_rtt_ms": statistics.fmean(values),
            "p95_rtt_ms": quantile(values, 0.95),
            "p99_rtt_ms": quantile(values, 0.99),
            "sla_violation_rate": sum(v > 100 for v in values) / len(values),
            "switches": sum(switches),
            "mean_effective_rtt_ms": statistics.fmean(effective),
            "p95_effective_rtt_ms": quantile(effective, 0.95),
            "effective_sla_violation_rate": sum(v > 100 for v in effective)
            / len(effective),
        }
        for key, value in calculated.items():
            assert math.isclose(value, saved[key], rel_tol=0, abs_tol=1e-9), (
                directory,
                name,
                key,
                value,
                saved[key],
            )
        metrics[name] = calculated
    minimum = [min(telemetry[t]) for t in origins]
    bound = sum(v > 100 for v in minimum) / len(minimum)
    assert math.isclose(bound, report["both_paths_bad_rate_lower_bound"])
    return {
        "scenario": report["scenario"],
        "seed": report["seed"],
        "source": "synthetic_uncalibrated",
        "input_files": {
            str(path): digest(path)
            for path in (telemetry_path, decisions_path, report_path)
        },
        "policies": metrics,
        "both_paths_bad_rate": bound,
        "pointwise_minimum_p95_ms": quantile(minimum, 0.95),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--suite", type=Path, default=Path("artifacts/planning-2026-10-31/benchmark")
    )
    parser.add_argument(
        "--high-rtt",
        type=Path,
        default=Path("artifacts/planning-2026-10-31/demo-high-rtt/experiment"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/mvp-evidence-2026-10-31/verified.json"),
    )
    args = parser.parse_args()
    runs = [
        check_run(args.suite / f"{scenario}-{seed}")
        for scenario in ("normal", "shared-stress")
        for seed in (41, 42, 43)
    ]
    high = check_run(args.high_rtt)
    # Exact illustrative identities. No draws, fitted distributions or measured gains.
    illustrative = {
        "required_reduction_fraction": (700 - 100) / 700,
        "independent_duplicate_miss_probability": 0.1 * 0.1,
        "fully_correlated_duplicate_miss_probability": 0.1,
        "payload_only_mbps": 1000 * 1200 * 8 / 1e6,
        "full_duplication_payload_only_mbps": 2 * 1000 * 1200 * 8 / 1e6,
        "serialization_1200_bytes_at_10mbps_ms": 1200 * 8 / 10e6 * 1000,
        "queue_600ms_at_10mbps_decimal_bytes": 0.6 * 10e6 / 8,
        "illustrative_probe_mbps": 2 * 10 * 2 * 128 * 8 / 1e6,
        "hedge_example_ack_ms": min(700, 20 + 60) + 1,
        "both_slow_hedge_example_ack_ms": min(700, 20 + 700) + 1,
    }
    result = {
        "scope": "Independent arithmetic recomputation of saved runs, not retraining",
        "limitations": [
            "Synthetic and uncalibrated; no RZD measurements",
            "Shared-stress and normal with the same seed share a shifted base series",
            "Previously inspected seeds; not an unseen test",
            "No causal benefit, live overhead or fault-localization claim",
        ],
        "audit_script_sha256": digest(Path(__file__)),
        "runs": runs,
        "high_rtt": high,
        "analytical_examples_not_measurements": illustrative,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    for run in runs:
        policies = run["policies"]
        names = ("previous_rtt_constrained", "nn_rtt_constrained", "risk_selector")
        rates = [round(100 * policies[n]["sla_violation_rate"], 3) for n in names]
        print(run["scenario"], run["seed"], rates)
    print("High RTT minimum p95, ms:", high["pointwise_minimum_p95_ms"])
    print("Verified:", args.output)


if __name__ == "__main__":
    main()
