"""Reproducible offline experiment CLI; never touches live routing."""

import argparse
import csv
import hashlib
import json
import platform
from pathlib import Path

import numpy as np
import torch

from quantum_link_optimizer.dataset import make_windows
from quantum_link_optimizer.evaluation import evaluate_policies, forecast_metrics
from quantum_link_optimizer.model import predict, train_model
from quantum_link_optimizer.telemetry import (
    SOURCE,
    generate_telemetry,
    read_telemetry,
    write_telemetry,
)


def run_experiment(
    output: Path,
    samples: int = 4000,
    seed: int = 42,
    window: int = 16,
    epochs: int = 60,
    switching_cost_ms: float = 3.0,
    sla_ms: float = 100.0,
    input_path: Path | None = None,
) -> dict:
    if not np.isfinite(sla_ms) or sla_ms <= 0:
        raise ValueError("SLA threshold must be finite and positive")
    if not np.isfinite(switching_cost_ms) or switching_cost_ms < 0:
        raise ValueError("Switching cost must be finite and nonnegative")
    values = (
        read_telemetry(input_path) if input_path else generate_telemetry(samples, seed)
    )
    data = make_windows(values, window)
    model, history = train_model(data, seed, epochs, sla_ms)
    all_forecast, all_probability = predict(model, data)
    test = data.mask("test")
    actual = data.y[test]
    forecast = all_forecast[test]
    probability = all_probability[test]
    previous = values[data.indices[test] - 1]
    policies, choices = evaluate_policies(
        actual,
        previous,
        forecast,
        switching_cost_ms,
        sla_ms,
    )
    output.mkdir(parents=True, exist_ok=True)
    telemetry_path = output / "telemetry.csv"
    write_telemetry(telemetry_path, values)
    configuration = {
        "samples": len(values),
        "seed": seed,
        "window": window,
        "epochs": epochs,
        "switching_cost_ms": switching_cost_ms,
        "sla_ms": sla_ms,
        "input_path": str(input_path) if input_path else None,
    }
    report = {
        "source": SOURCE,
        "warning": "Synthetic, uncalibrated; not evidence of real QKD/PTP performance",
        "configuration": configuration,
        "versions": {
            "python": platform.python_version(),
            "numpy": np.__version__,
            "torch": torch.__version__,
        },
        "telemetry_sha256": hashlib.sha256(telemetry_path.read_bytes()).hexdigest(),
        "split": {
            "train_target_range": [window, data.train_end - 1],
            "validation_target_range": [data.train_end, data.val_end - 1],
            "test_target_range": [data.val_end, len(values) - 1],
            "scaler_fit_row_range": [0, data.train_end - 1],
        },
        "forecast": {
            "neural_network": forecast_metrics(actual, forecast, probability, sla_ms),
            "persistence": forecast_metrics(
                actual,
                previous,
                (previous > sla_ms).astype(float),
                sla_ms,
            ),
            "train_climatology_brier_score": float(
                np.mean(
                    (
                        (values[: data.train_end] > sla_ms).mean(axis=0)
                        - (actual > sla_ms)
                    )
                    ** 2,
                )
            ),
        },
        "policies": policies,
        "training": {
            "epochs_run": len(history),
            "best_epoch": min(history, key=lambda row: row["val_loss"])["epoch"],
            "history": history,
        },
    }
    checkpoint = {
        "state_dict": model.state_dict(),
        "window": window,
        "mean": data.mean.tolist(),
        "scale": data.scale.tolist(),
        "configuration": configuration,
    }
    torch.save(checkpoint, output / "model.pt")
    (output / "report.json").write_text(
        json.dumps(report, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    with (output / "predictions.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(
            [
                "sample",
                "source",
                "actual_0_ms",
                "actual_1_ms",
                "previous_0_ms",
                "previous_1_ms",
                "forecast_0_ms",
                "forecast_1_ms",
                "degradation_probability_0",
                "degradation_probability_1",
                *choices,
            ]
        )
        for t, index in enumerate(data.indices[test]):
            writer.writerow(
                [
                    index,
                    SOURCE,
                    *actual[t],
                    *previous[t],
                    *forecast[t],
                    *probability[t],
                    *(selected[t] for selected in choices.values()),
                ]
            )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline synthetic RTT experiment")
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("generate", help="Write labeled synthetic CSV")
    generate.add_argument("--output", type=Path, required=True)
    generate.add_argument("--samples", type=int, default=4000)
    generate.add_argument("--seed", type=int, default=42)
    experiment = commands.add_parser("experiment", help="Generate, train and evaluate")
    experiment.add_argument("--output", type=Path, default=Path("artifacts/run-42"))
    experiment.add_argument("--input", type=Path)
    experiment.add_argument("--samples", type=int, default=4000)
    experiment.add_argument("--seed", type=int, default=42)
    experiment.add_argument("--window", type=int, default=16)
    experiment.add_argument("--epochs", type=int, default=60)
    experiment.add_argument("--switching-cost-ms", type=float, default=3.0)
    experiment.add_argument("--sla-ms", type=float, default=100.0)
    args = parser.parse_args()
    try:
        if args.command == "generate":
            write_telemetry(args.output, generate_telemetry(args.samples, args.seed))
            print(f"Synthetic telemetry: {args.output}")
        else:
            report = run_experiment(
                args.output,
                args.samples,
                args.seed,
                args.window,
                args.epochs,
                args.switching_cost_ms,
                args.sla_ms,
                args.input,
            )
            print(
                json.dumps(
                    {key: value for key, value in report.items() if key != "training"},
                    indent=2,
                    allow_nan=False,
                )
            )
    except ValueError as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
