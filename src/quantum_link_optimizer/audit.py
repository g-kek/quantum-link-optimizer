"""Describe measured ping RTT and replay baselines without fitting synthetic ML."""

import hashlib
from dataclasses import asdict
from pathlib import Path

import numpy as np

from quantum_link_optimizer.artifacts import dump_json, runtime_metadata
from quantum_link_optimizer.observations import read_observations
from quantum_link_optimizer.report import render_report
from quantum_link_optimizer.risk import RiskConfig, RiskSelector

AUDIT_CONFIG = RiskConfig(violation_penalty_ms=0.0)


def rtt_summary(values):
    values = [v for v in values if v is not None]
    return {
        "available": len(values),
        "min_rtt_ms": float(np.min(values)) if values else None,
        "mean_rtt_ms": float(np.mean(values)) if values else None,
        "p95_rtt_ms": float(np.quantile(values, 0.95)) if values else None,
        "p99_rtt_ms": float(np.quantile(values, 0.99)) if values else None,
    }


def feasibility(points, sla_ms):
    pairs = [p["rtt"] for p in points if all(v is not None for v in p["rtt"])]
    minima = [min(row) for row in pairs]
    bad = sum(v > sla_ms for v in minima)
    return {
        "paired_samples": len(pairs),
        "total_origins": len(points),
        "paired_coverage": len(pairs) / len(points) if points else None,
        "both_paths_bad_samples": bad,
        "both_paths_bad_rate_available_pairs": bad / len(pairs) if pairs else None,
        "oracle_min_p95_ms_available_pairs": (
            float(np.quantile(minima, 0.95)) if minima else None
        ),
        "interpretation": (
            "Lower bound only on observed pairs, ignoring switching and load effects. "
            "Missing or timed-out RTT does not imply a successful delivery."
        ),
    }


def path_summaries(points, sla_ms):
    result = []
    for channel in (0, 1):
        values = [p["rtt"][channel] for p in points]
        known = [v for v in values if v is not None]
        result.append(
            {
                **rtt_summary(values),
                "path": channel,
                "origins": len(points),
                "coverage": len(known) / len(points),
                "timeouts": sum(p["status"][channel] == "timeout" for p in points),
                "missing": sum(p["status"][channel] == "missing" for p in points),
                "sla_violation_rate": (
                    sum(v > sla_ms for v in known) / len(known) if known else None
                ),
            }
        )
    return result


def audit_policy(points, name, config, sla_ms):
    raw, effective = [], []
    switches = decisions = timeouts = missing = 0
    previous = None
    for point in points:
        selected = point["choices"][name]
        if selected is None:
            continue
        decisions += 1
        switched = previous is not None and selected != previous
        switches += switched
        previous = selected
        value = point["rtt"][selected]
        if value is None:
            timeouts += point["status"][selected] == "timeout"
            missing += point["status"][selected] == "missing"
        else:
            raw.append(value)
            effective.append(value + config.switching_cost_ms * switched)
    return {
        **rtt_summary(raw),
        "samples": len(points),
        "abstentions": len(points) - decisions,
        "selected_outcome_coverage": len(raw) / len(points),
        "selected_timeouts": timeouts,
        "selected_missing": missing,
        "sla_violation_rate": (
            sum(v > sla_ms for v in raw) / len(raw) if raw else None
        ),
        "mean_effective_rtt_ms": float(np.mean(effective)) if effective else None,
        "effective_sla_violation_rate": (
            sum(v > sla_ms for v in effective) / len(effective) if effective else None
        ),
        "switches": switches,
    }


def run_audit(
    input_path: Path,
    output: Path,
    sla_ms=100.0,
    max_gap_seconds=2.0,
    config=AUDIT_CONFIG,
):
    if not np.isfinite(sla_ms) or sla_ms <= 0:
        raise ValueError("SLA must be positive and finite")
    if not np.isfinite(max_gap_seconds) or max_gap_seconds <= 0:
        raise ValueError("Max gap must be positive and finite")
    observations = read_observations(input_path)
    if len(observations) < 2:
        raise ValueError("Ping audit requires at least two observation times")
    selectors = {
        "previous_rtt_constrained": RiskSelector(config),
        "ewma_constrained": RiskSelector(config),
    }
    points, smoothed = [], None
    for i, actual in enumerate(observations):
        complete = actual.status == ("ok", "ok")
        if i:
            past = observations[i - 1]
            fresh = actual.time_seconds - past.time_seconds <= max_gap_seconds
            choices = {"fixed_path_0": 0, "fixed_path_1": 1}
            reasons = {"fixed_path_0": "fixed_path", "fixed_path_1": "fixed_path"}
            for name, selector in selectors.items():
                if past.status != ("ok", "ok") or smoothed is None:
                    choices[name], reasons[name] = None, "abstain_missing_history"
                elif not fresh:
                    choices[name], reasons[name] = None, "abstain_stale_history"
                else:
                    prediction = past.rtt if name.startswith("previous") else smoothed
                    choices[name], reasons[name] = selector.step(
                        actual.sample, [0.0, 0.0], prediction
                    )
            # Only the predeclared timestamp and the past are used above.
            points.append(
                {
                    "sample": actual.sample,
                    "timestamp": actual.timestamp,
                    "rtt": list(actual.rtt),
                    "status": list(actual.status),
                    "choices": choices,
                    "reasons": reasons,
                    "information_cutoff": past.timestamp,
                }
            )
            if not fresh:
                smoothed = None
        if complete:
            smoothed = (
                np.asarray(actual.rtt)
                if smoothed is None
                else (0.3 * np.asarray(actual.rtt) + 0.7 * smoothed)
            )
        else:
            smoothed = None
    report = {
        "kind": "ping_audit",
        "title": "Анализ ping RTT двух путей",
        "source_label": "Внешние измерения · происхождение указано в CSV",
        "runtime": runtime_metadata(),
        "input_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
        "sources": sorted({o.source for o in observations}),
        "provenance": sorted({p for o in observations for p in o.provenance if p}),
        "samples_total": len(observations),
        "sla_ms": sla_ms,
        "configuration": asdict(config),
        "max_gap_seconds": max_gap_seconds,
        "evaluation_note": (
            "All observations except the first; no training or model transfer. "
            "Baselines use past RTT only; EWMA alpha=0.3. "
            "RTT metrics are available-only; read coverage and losses alongside them. "
            "Switch cost is a scenario parameter, not a measured network action."
        ),
        "feasibility": feasibility(points, sla_ms),
        "paths": path_summaries(points, sla_ms),
        "policies": {
            name: audit_policy(points, name, config, sla_ms)
            for name in points[0]["choices"]
        },
        "points": points,
    }
    output.mkdir(parents=True, exist_ok=True)
    dump_json(output / "report.json", report)
    render_report(report, output)
    return report
