"""Freeze MLP/LSTM before new matched live trials; never fit on held-out episodes."""

import argparse
import asyncio
import json
import time
from collections import Counter
from functools import partial
from pathlib import Path

from quantum_link_optimizer.experiments.common import distribution, write_json
from quantum_link_optimizer.experiments.forecasting.model import evaluate, fit
from quantum_link_optimizer.experiments.forecasting.policy import ForecastPolicy
from quantum_link_optimizer.experiments.runtime import run_trial
from quantum_link_optimizer.experiments.scenarios import configuration
from quantum_link_optimizer.experiments.validation.metrics import (
    paired_bootstrap,
    trial_metrics,
)
from quantum_link_optimizer.selection.config import Settings
from quantum_link_optimizer.telemetry_lab.manifest import (
    begin,
    digest,
    finish,
    verify_files,
)
from quantum_link_optimizer.telemetry_lab.report import records

from .model import RecurrentPolicy, SequenceForecast, fit_sequences, sequences


def verify_development(development, plan):
    manifest = json.loads((development / "manifest.json").read_text())
    checks = verify_files(development)

    if manifest["status"] != "collected" or not checks or not all(checks.values()):
        raise ValueError("development evidence is incomplete or corrupted")

    source = json.loads((development / "scenario.json").read_text())
    reserved = set(plan["final_seeds"])
    known = set().union(
        *(source[k] for k in ("train_seeds", "validation_seeds", "final_seeds"))
    )

    if not reserved or reserved & known or len(reserved) != len(plan["final_seeds"]):
        raise ValueError("new final seeds overlap earlier experiments")

    training = records(development / "train.jsonl")
    validation = records(development / "validation.jsonl")

    if {r["episode_id"] for r in training} & {r["episode_id"] for r in validation}:
        raise ValueError("development episodes overlap")

    return training, validation


def sequence_quality(model, validation):
    examples = sequences(validation, model.window)
    predictions = model.predict_sequences([r["sequence"] for r in examples])
    result = {}

    for index, target in enumerate(("late_command", "empty_key_pool")):
        errors = [
            (r["targets"][index] - p[index]) ** 2
            for r, p in zip(examples, predictions, strict=True)
            if r["targets"][index] is not None
        ]
        result[target] = {"count": len(errors), "brier": sum(errors) / len(errors)}

    return result


async def experiment(development, plan, output):
    training, validation = verify_development(development, plan)
    manifest = begin(output, plan)
    manifest["status"] = "failed"

    try:
        mlp = fit(training, seed=plan["model_seed"], epochs=plan["epochs"])
        lstm = fit_sequences(
            training,
            mlp,
            window=plan["window"],
            hidden_size=plan["hidden_size"],
            seed=plan["model_seed"],
            epochs=plan["epochs"],
        )
        episodes = {r["episode_id"] for r in training}
        mlp.save(output / "mlp.json", episodes)
        lstm.save(output / "lstm.json", episodes)
        lstm = SequenceForecast.load(output / "lstm.json")
        quality = {
            "mlp": evaluate(mlp, training, validation),
            "lstm": sequence_quality(lstm, validation),
        }
        write_json(output / "validation-quality.json", quality)
        frozen = {
            "plan": plan,
            "development_manifest_sha256": digest(development / "manifest.json"),
            "source_sha256": digest(output / "source.zip"),
            "models": {
                name: digest(output / f"{name}.json") for name in ("mlp", "lstm")
            },
            "selection": "Fixed architecture and epochs, no tuning on new final trials",
        }
        write_json(output / "policy-lock.json", frozen)
        lock_hash = digest(output / "policy-lock.json")
        print("Frozen MLP/LSTM, starting new held-out trials", flush=True)
        rows = []

        for case in plan["cases"]:
            for seed in plan["final_seeds"]:
                for policy in ("ewma", "mlp", "lstm"):
                    config = configuration(
                        case,
                        seed,
                        count=plan["commands"],
                        interval_s=plan["interval_s"],
                    )
                    settings = Settings(min_hold_ms=0, switch_margin_ms=5)
                    factory = None

                    if policy == "mlp":
                        factory = partial(
                            ForecastPolicy,
                            model=mlp,
                            penalty_ms=plan["prediction_penalty_ms"],
                        )
                    elif policy == "lstm":
                        factory = partial(
                            RecurrentPolicy,
                            model=lstm,
                            penalty_ms=plan["prediction_penalty_ms"],
                        )

                    directory = output / "final" / f"{case}-{seed}-{policy}"
                    summary = await run_trial(config, directory, settings, factory)
                    measured = trial_metrics(directory, summary, case)
                    events = records(directory / "sender.jsonl")
                    states = Counter(
                        e["state"] for e in events if e["event"] == "incident_opened"
                    )

                    if digest(directory / "source.zip") != frozen["source_sha256"]:
                        raise ValueError("trial code changed after freeze")

                    if not all(verify_files(directory).values()):
                        raise ValueError("trial artifacts failed verification")

                    if any(e["event"] == "diagnostic_error" for e in events):
                        raise ValueError("diagnostic monitor failed")

                    rows.append(
                        {
                            "case": case,
                            "seed": seed,
                            "policy": policy,
                            "relative_path": str(directory.relative_to(output)),
                            "manifest_sha256": digest(directory / "manifest.json"),
                            "incident_openings": dict(states),
                            **measured,
                        }
                    )
                    print(
                        f"{case}-{seed}-{policy}: "
                        f"{measured['timely_executed']}/{measured['offered']}, "
                        f"{dict(states)}",
                        flush=True,
                    )

        for name, expected in frozen["models"].items():
            if digest(output / f"{name}.json") != expected:
                raise ValueError("model changed during final")

        if digest(output / "policy-lock.json") != lock_hash:
            raise ValueError("policy lock changed during final")

        latency = {}
        sample = sequences(validation, lstm.window)[-1]["sequence"]

        for name, predict in (
            ("mlp", lambda: mlp.predict([sample[-1]] * 2)),
            ("lstm", lambda: lstm.predict_sequences([sample] * 2)),
        ):
            predict()
            elapsed = []

            for _ in range(100):
                start = time.perf_counter_ns()
                predict()
                elapsed.append((time.perf_counter_ns() - start) / 1000)

            latency[name] = distribution(elapsed)

        report = {
            "schema_version": 1,
            "plan": plan,
            "freeze": frozen,
            "policy_lock_sha256": lock_hash,
            "validation": quality,
            "trials": rows,
            "lstm_vs_ewma": paired_bootstrap(rows, "lstm", "ewma"),
            "lstm_vs_mlp": paired_bootstrap(rows, "lstm", "mlp"),
            "two_path_inference_us": latency,
            "scope": "Small co-located laboratory experiment, not an operator pilot",
            "diagnostic_scope": (
                "Condition opening counts, not a root-cause accuracy score"
            ),
            "model_scope": (
                "Same two prediction targets, LSTM is not a cause classifier"
            ),
            "recommend_lstm": False,
        }
        write_json(output / "recurrent-report.json", report)
        manifest["status"] = "collected"
    finally:
        finish(output, manifest)

    return report


def main():
    parser = argparse.ArgumentParser(
        description="Causal MLP/LSTM matched live comparison"
    )
    parser.add_argument("--development", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(
        experiment(args.development, json.loads(args.plan.read_text()), args.output)
    )


if __name__ == "__main__":
    main()
