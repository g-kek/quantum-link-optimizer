"""Causal risk-aware decisions with explicit units and hard dwell constraints."""

from dataclasses import asdict, dataclass, replace
from pathlib import Path

import numpy as np
import torch

from quantum_link_optimizer.evaluation import policy_metrics, select_channels
from quantum_link_optimizer.telemetry import SOURCE, generate_telemetry
from quantum_link_optimizer.warning import (
    WarningForecaster,
    dump_json,
    run_warning,
    warning_predict,
)


@dataclass(frozen=True)
class RiskConfig:
    violation_penalty_ms: float = 100.0
    switching_cost_ms: float = 3.0
    hysteresis_ms: float = 3.0
    min_hold_samples: int = 5

    def __post_init__(self):
        costs = (self.violation_penalty_ms, self.switching_cost_ms, self.hysteresis_ms)
        if any(not np.isfinite(v) or v < 0 for v in costs):
            raise ValueError("Risk costs must be finite and nonnegative")
        if not isinstance(self.min_hold_samples, int) or self.min_hold_samples < 1:
            raise ValueError("Minimum hold must be a positive integer")


DEFAULT_RISK_CONFIG = RiskConfig()


class RiskSelector:
    def __init__(self, config=DEFAULT_RISK_CONFIG):
        self.config = config
        self.current = None
        self.since = None

    def step(self, sample, probability, rtt):
        probability, rtt = np.asarray(probability), np.asarray(rtt)
        if (
            probability.shape != (2,)
            or rtt.shape != (2,)
            or not np.isfinite(probability).all()
            or not np.isfinite(rtt).all()
            or (rtt <= 0).any()
            or (probability < 0).any()
            or (probability > 1).any()
        ):
            return None, "abstain_invalid_prediction"
        cfg = self.config
        objective = rtt + cfg.violation_penalty_ms * probability
        if self.current is None:
            self.current = int(np.argmin(objective))
            self.since = sample
            return self.current, "initial_minimum_objective"
        if sample - self.since < cfg.min_hold_samples:
            return self.current, "minimum_hold"
        other = 1 - self.current
        improvement = objective[self.current] - (
            objective[other] + cfg.switching_cost_ms
        )
        if improvement > cfg.hysteresis_ms:
            self.current, self.since = other, sample
            return self.current, "objective_improvement"
        return self.current, "hysteresis"


def load_warning(path):
    checkpoint = torch.load(path, weights_only=True, map_location="cpu")
    if not isinstance(checkpoint, dict):
        raise ValueError("Warning checkpoint must be a dictionary")
    required = {
        "window",
        "horizons",
        "mean",
        "scale",
        "state_dict",
        "source",
        "train_end",
        "val_end",
        "samples",
        "sla_ms",
    }
    if missing := required - checkpoint.keys():
        raise ValueError(f"Warning checkpoint missing fields: {sorted(missing)}")
    if checkpoint.get("schema_version", 1) != 1:
        raise ValueError("Unsupported warning checkpoint schema version")
    for field in ("window", "train_end", "val_end", "samples"):
        if type(checkpoint[field]) is not int or checkpoint[field] < 1:
            raise ValueError(f"Invalid checkpoint {field}")
    if not (
        checkpoint["window"]
        < checkpoint["train_end"]
        < checkpoint["val_end"]
        < checkpoint["samples"]
    ):
        raise ValueError("Invalid checkpoint chronological boundaries")
    horizons = checkpoint["horizons"]
    if (
        not isinstance(horizons, (list, tuple))
        or any(type(h) is not int or h < 1 for h in horizons)
        or 1 not in horizons
        or len(set(horizons)) != len(horizons)
    ):
        raise ValueError("Checkpoint horizons must be unique and include horizon 1")
    try:
        mean, scale = (
            np.asarray(checkpoint["mean"], float),
            np.asarray(checkpoint["scale"], float),
        )
        sla = float(checkpoint["sla_ms"])
    except (ValueError, TypeError) as error:
        raise ValueError("Invalid checkpoint scaler or SLA") from error
    if (
        mean.shape != (2,)
        or scale.shape != (2,)
        or not np.isfinite(mean).all()
        or not np.isfinite(scale).all()
        or (scale <= 0).any()
        or not np.isfinite(sla)
        or sla <= 0
    ):
        raise ValueError(
            "Checkpoint scaler must be finite with positive scales and SLA"
        )
    if checkpoint["source"] != SOURCE:
        raise ValueError("Unsupported warning model training source")
    state = checkpoint["state_dict"]
    if (
        not isinstance(state, dict)
        or not state
        or any(
            not isinstance(v, torch.Tensor) or not torch.isfinite(v).all()
            for v in state.values()
        )
    ):
        raise ValueError("Checkpoint weights must be finite tensors")
    try:
        RiskConfig(**checkpoint.get("risk_configuration", {}))
        model = WarningForecaster(checkpoint["window"], horizons)
        model.load_state_dict(state)
    except (TypeError, RuntimeError) as error:
        raise ValueError(f"Invalid warning checkpoint: {error}") from error
    model.eval()
    return model, checkpoint


def risk_choices(indices, probability, forecast, config=DEFAULT_RISK_CONFIG):
    selector = RiskSelector(config)
    return np.asarray(
        [
            selector.step(int(t), p, r)[0]
            for t, p, r in zip(indices, probability, forecast, strict=True)
        ],
        dtype=np.int64,
    )


def synthetic_scenario(samples, seed, scenario):
    values = generate_telemetry(samples, seed)
    if scenario == "shared-stress":
        values += 25.0
    elif scenario != "normal":
        raise ValueError("Unknown synthetic scenario")
    return values


def causal_ewma(values, indices, alpha=0.3):
    """Forecast t using only values through t-1, including the fitting prefix."""
    smoothed = np.asarray(values, dtype=float).copy()
    for t in range(1, len(values)):
        smoothed[t] = alpha * values[t] + (1 - alpha) * smoothed[t - 1]
    return smoothed[np.asarray(indices) - 1]


def point_choices(indices, forecast, config=DEFAULT_RISK_CONFIG):
    """Same dwell, hysteresis and switching cost; no probability penalty."""
    return risk_choices(
        indices,
        np.zeros_like(forecast),
        forecast,
        replace(config, violation_penalty_ms=0.0),
    )


def run_risk(
    output: Path,
    samples=4000,
    seed=42,
    epochs=40,
    scenario="normal",
    config=DEFAULT_RISK_CONFIG,
):
    values = synthetic_scenario(samples, seed, scenario)
    run_warning(output, samples, seed, epochs, values=values)
    model, checkpoint = load_warning(output / "warning-model.pt")
    checkpoint["risk_configuration"] = asdict(config)
    checkpoint["scenario"] = scenario
    torch.save(checkpoint, output / "warning-model.pt")
    indices = np.arange(checkpoint["val_end"], len(values))
    mean, scale = np.array(checkpoint["mean"]), np.array(checkpoint["scale"])
    window = checkpoint["window"]
    x = np.stack([(values[t - window : t] - mean) / scale for t in indices])
    forecast, probabilities = warning_predict(model, x, mean, scale)
    probability = probabilities[:, checkpoint["horizons"].index(1)]
    previous, actual = values[indices - 1], values[indices]
    choices = {
        "risk_selector": risk_choices(indices, probability, forecast, config),
        "previous_rtt_selector": select_channels(previous, config.switching_cost_ms),
        "previous_rtt_constrained": point_choices(indices, previous, config),
        "ewma_constrained": point_choices(
            indices, causal_ewma(values, indices), config
        ),
        "nn_rtt_constrained": point_choices(indices, forecast, config),
        "fixed_path_0": np.zeros(len(indices), dtype=np.int64),
        "fixed_path_1": np.ones(len(indices), dtype=np.int64),
        "pointwise_oracle_evaluation_only": np.argmin(actual, axis=1),
    }
    metrics = {}
    for name, selected in choices.items():
        result = policy_metrics(actual, selected, config.switching_cost_ms)
        result["mean_realized_cost_ms"] = (
            result["mean_effective_rtt_ms"]
            + config.violation_penalty_ms * result["sla_violation_rate"]
        )
        metrics[name] = result
    report = {
        "source": checkpoint["source"],
        "seed": seed,
        "scenario": scenario,
        "configuration": asdict(config),
        "configuration_source": "predeclared, no test optimization",
        "comparison_note": (
            "Constrained previous/EWMA/NN and risk share dwell, hysteresis and "
            "switching cost. Point selectors omit the probability penalty. "
            "EWMA alpha=0.3 is predeclared; "
            "previous_rtt_selector is legacy unrestricted."
        ),
        "objective": "predicted RTT + penalty_ms * P(RTT(t)>100) + switch_cost_ms",
        "test_first_origin": int(indices[0]),
        "test_last_origin": int(indices[-1]),
        "both_paths_bad_rate_lower_bound": float(np.mean((actual > 100).all(axis=1))),
        "policies": metrics,
    }
    dump_json(output / "risk-report.json", report)
    import csv

    with (output / "risk-decisions.csv").open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "sample",
                "probability_0",
                "probability_1",
                "forecast_0_ms",
                "forecast_1_ms",
                *choices,
            ]
        )
        for i, t in enumerate(indices):
            writer.writerow(
                [t, *probability[i], *forecast[i], *(c[i] for c in choices.values())]
            )
    return report
