"""Offline selectors and unconditional full-horizon RTT metrics."""

import numpy as np


def select_channels(forecast: np.ndarray, switching_cost_ms: float) -> np.ndarray:
    choices = np.empty(len(forecast), dtype=np.int64)
    previous = None
    for t, row in enumerate(forecast):
        costs = row.copy()
        if previous is not None:
            costs += switching_cost_ms * (np.arange(2) != previous)
        previous = int(np.argmin(costs))
        choices[t] = previous
    return choices


def hindsight_oracle(actual: np.ndarray, switching_cost_ms: float) -> np.ndarray:
    accumulated = actual[0].copy()
    parents = np.zeros_like(actual, dtype=np.int64)
    for t in range(1, len(actual)):
        costs = accumulated[:, None] + switching_cost_ms * (
            np.arange(2)[:, None] != np.arange(2)[None, :]
        )
        parents[t] = np.argmin(costs, axis=0)
        accumulated = actual[t] + np.min(costs, axis=0)
    choices = np.empty(len(actual), dtype=np.int64)
    choices[-1] = np.argmin(accumulated)
    for t in range(len(actual) - 1, 0, -1):
        choices[t - 1] = parents[t, choices[t]]
    return choices


def policy_metrics(
    actual: np.ndarray,
    choices: np.ndarray,
    switching_cost_ms: float,
    sla_ms: float = 100.0,
) -> dict:
    selected = actual[np.arange(len(actual)), choices]
    switches = np.r_[False, choices[1:] != choices[:-1]]
    effective = selected + switching_cost_ms * switches
    return {
        "samples": len(actual),
        "mean_rtt_ms": float(selected.mean()),
        "p95_rtt_ms": float(np.quantile(selected, 0.95)),
        "sla_violation_rate": float(np.mean(selected > sla_ms)),
        "mean_effective_rtt_ms": float(effective.mean()),
        "p95_effective_rtt_ms": float(np.quantile(effective, 0.95)),
        "effective_sla_violation_rate": float(np.mean(effective > sla_ms)),
        "switches": int(switches.sum()),
        "switch_rate": float(switches.sum() / max(1, len(actual) - 1)),
    }


def forecast_metrics(
    actual: np.ndarray,
    forecast: np.ndarray,
    probability: np.ndarray,
    sla_ms: float,
) -> dict:
    events = actual > sla_ms
    clipped = np.clip(probability, 1e-7, 1 - 1e-7)
    return {
        "mae_ms": float(np.mean(np.abs(actual - forecast))),
        "rmse_ms": float(np.sqrt(np.mean((actual - forecast) ** 2))),
        "per_channel_mae_ms": np.mean(np.abs(actual - forecast), axis=0).tolist(),
        "degradation_brier_score": float(np.mean((probability - events) ** 2)),
        "degradation_log_loss": float(
            -np.mean(
                events * np.log(clipped) + (~events) * np.log(1 - clipped),
            )
        ),
        "observed_degradation_rate": events.mean(axis=0).tolist(),
        "mean_degradation_probability": probability.mean(axis=0).tolist(),
    }


def evaluate_policies(
    actual: np.ndarray,
    previous: np.ndarray,
    forecast: np.ndarray,
    switching_cost_ms: float,
    sla_ms: float,
) -> tuple[dict, dict[str, np.ndarray]]:
    if not np.isfinite(switching_cost_ms) or switching_cost_ms < 0:
        raise ValueError("Switching cost must be finite and nonnegative")
    choices = {
        "fixed_channel_0": np.zeros(len(actual), dtype=np.int64),
        "fixed_channel_1": np.ones(len(actual), dtype=np.int64),
        "previous_observation_selector": select_channels(previous, switching_cost_ms),
        "nn_selector": select_channels(forecast, switching_cost_ms),
        "hindsight_oracle": hindsight_oracle(actual, switching_cost_ms),
    }
    metrics = {
        name: policy_metrics(actual, selected, switching_cost_ms, sla_ms)
        for name, selected in choices.items()
    }
    return metrics, choices
