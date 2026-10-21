"""Offline streaming shadow: flush decision before consuming its outcome."""

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

import numpy as np
import torch

from quantum_link_optimizer.observations import Observation, read_observations
from quantum_link_optimizer.risk import (
    DEFAULT_RISK_CONFIG,
    RiskConfig,
    RiskSelector,
    load_warning,
)
from quantum_link_optimizer.telemetry import SOURCE, read_telemetry
from quantum_link_optimizer.warning import dump_json, warning_predict


def synthetic_observations(values):
    return [
        Observation(
            t,
            f"synthetic:{t}",
            float(t),
            tuple(row),
            ("ok", "ok"),
            SOURCE,
            ("local synthetic generator", "local synthetic generator"),
        )
        for t, row in enumerate(values)
    ]


def decide(
    history, target_seconds, sample, model, checkpoint, selector, max_gap_seconds
):
    window = checkpoint["window"]
    if len(history) < window:
        return None, None, None, "abstain_insufficient_history"
    past = history[-window:]
    if any(o.status != ("ok", "ok") for o in past):
        return None, None, None, "abstain_missing_history"
    times = [o.time_seconds for o in past] + [target_seconds]
    gaps = np.diff(times)
    if (gaps <= 0).any() or (gaps > max_gap_seconds).any():
        return None, None, None, "abstain_stale_history"
    mean, scale = np.array(checkpoint["mean"]), np.array(checkpoint["scale"])
    x = ((np.array([o.rtt for o in past]) - mean) / scale)[None]
    rtt, probability = warning_predict(model, x, mean, scale)
    selected, reason = selector.step(
        sample, probability[0, checkpoint["horizons"].index(1)], rtt[0]
    )
    return selected, rtt[0].tolist(), probability[0].tolist(), reason


def replay_stream(
    schedule,
    outcomes,
    model,
    checkpoint,
    log_path,
    start_sample,
    max_gap_seconds=2.0,
    config=DEFAULT_RISK_CONFIG,
):
    if not np.isfinite(max_gap_seconds) or max_gap_seconds <= 0:
        raise ValueError("Max gap must be positive and finite")
    selector, history = RiskSelector(config), []
    outcomes = iter(outcomes)
    decisions, abstentions, evaluated = 0, 0, 0
    raw, effective, violations = [], [], []
    both_known, both_bad, switches = 0, 0, 0
    previous_choice = None
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as stream:

        def emit(record):
            stream.write(json.dumps(record, allow_nan=False) + "\n")
            stream.flush()

        for sample, timestamp, seconds in schedule:
            selection = None
            if sample >= start_sample:
                selection, rtt, probability, reason = decide(
                    history,
                    seconds,
                    sample,
                    model,
                    checkpoint,
                    selector,
                    max_gap_seconds,
                )
                decisions += 1
                abstentions += selection is None
                emit(
                    {
                        "kind": "decision",
                        "sample": sample,
                        "timestamp": timestamp,
                        "information_cutoff": history[-1].timestamp
                        if history
                        else None,
                        "forecast_rtt_ms": rtt,
                        "probability_by_horizon": probability,
                        "horizons_samples": checkpoint["horizons"],
                        "decision": selection,
                        "reason": reason,
                        "network_action": "none_shadow_only",
                    }
                )
            # The iterator cannot expose the current outcome before the flush above.
            actual = next(outcomes)
            if (actual.sample, actual.timestamp, actual.time_seconds) != (
                sample,
                timestamp,
                seconds,
            ):
                raise ValueError("Outcome does not match predeclared schedule")
            if sample >= start_sample:
                known = actual.status == ("ok", "ok")
                both_known += known
                both_bad += known and min(actual.rtt) > checkpoint["sla_ms"]
                selected_rtt = actual.rtt[selection] if selection is not None else None
                switched = (
                    selection is not None
                    and previous_choice is not None
                    and selection != previous_choice
                )
                switches += switched
                effective_rtt = None
                if selected_rtt is not None:
                    evaluated += 1
                    effective_rtt = selected_rtt + config.switching_cost_ms * switched
                    raw.append(selected_rtt)
                    effective.append(effective_rtt)
                    violations.append(selected_rtt > checkpoint["sla_ms"])
                if selection is not None:
                    previous_choice = selection
                emit(
                    {
                        "kind": "evaluation",
                        "sample": sample,
                        "timestamp": timestamp,
                        "source": actual.source,
                        "provenance": actual.provenance,
                        "status": actual.status,
                        "actual_rtt_ms": actual.rtt,
                        "selected_actual_rtt_ms": selected_rtt,
                        "selected_effective_rtt_ms": effective_rtt,
                        "selected_outcome_available": selected_rtt is not None,
                        "counterfactual_available": known,
                        "oracle_min_rtt_ms_evaluation_only": min(actual.rtt)
                        if known
                        else None,
                        "timeout_sla_interpretation": "unknown; no threshold",
                    }
                )
            history.append(actual)
            history = history[-checkpoint["window"] :]
    return {
        "mode": "offline_shadow_no_network_actions",
        "decision_origins": decisions,
        "abstentions": abstentions,
        "evaluated_selected_outcomes": evaluated,
        "selected_outcome_coverage": evaluated / decisions if decisions else None,
        "mean_selected_rtt_ms_available_only": float(np.mean(raw)) if raw else None,
        "p95_selected_rtt_ms_available_only": float(np.quantile(raw, 0.95))
        if raw
        else None,
        "sla_violation_rate_available_only": float(np.mean(violations))
        if violations
        else None,
        "mean_effective_rtt_ms_available_only": float(np.mean(effective))
        if effective
        else None,
        "effective_sla_violation_rate_available_only": float(
            np.mean(np.array(effective) > checkpoint["sla_ms"])
        )
        if effective
        else None,
        "both_path_outcomes_available": both_known,
        "both_paths_bad_rate_on_available_pairs": both_bad / both_known
        if both_known
        else None,
        "switches": switches,
        "configuration": asdict(config),
        "model_fixed": True,
    }


def run_shadow(
    model_path: Path,
    input_path: Path,
    output: Path,
    external=False,
    allow_domain_shift=False,
    start_sample=None,
    max_gap_seconds=2.0,
):
    torch.set_num_threads(1)
    model, checkpoint = load_warning(model_path)
    if external and not allow_domain_shift:
        raise ValueError(
            "Real input requires --allow-domain-shift; synthetic model unvalidated"
        )
    if external:
        observations = read_observations(input_path)
        start = observations[0].sample if start_sample is None else start_sample
    else:
        values = read_telemetry(input_path)
        observations = synthetic_observations(values)
        start = checkpoint["val_end"] if start_sample is None else start_sample
        if start < checkpoint["val_end"]:
            raise ValueError("Replay must start after the model's validation prefix")
        prefix_hash = hashlib.sha256(
            np.ascontiguousarray(values[: checkpoint["val_end"]]).tobytes()
        ).hexdigest()
        if (
            checkpoint.get("fit_prefix_sha256")
            and checkpoint["fit_prefix_sha256"] != prefix_hash
        ):
            raise ValueError(
                "Synthetic replay does not match saved training/validation prefix"
            )
    if not observations[0].sample <= start <= observations[-1].sample:
        raise ValueError("Replay start is outside telemetry")
    output.mkdir(parents=True, exist_ok=True)
    schedule = [(o.sample, o.timestamp, o.time_seconds) for o in observations]
    report = replay_stream(
        schedule,
        iter(observations),
        model,
        checkpoint,
        output / "shadow.jsonl",
        start,
        max_gap_seconds,
        RiskConfig(**checkpoint.get("risk_configuration", {})),
    )
    report.update(
        {
            "input_source": sorted({o.source for o in observations}),
            "input_sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(),
            "model_sha256": hashlib.sha256(model_path.read_bytes()).hexdigest(),
            "model_training_source": checkpoint["source"],
            "domain_shift_unvalidated": external,
            "prefix_verified": not external and "fit_prefix_sha256" in checkpoint,
            "max_gap_seconds": max_gap_seconds,
            "cadence_note": "No resampling; horizons are samples, not seconds",
        }
    )
    dump_json(output / "shadow-report.json", report)
    return report
