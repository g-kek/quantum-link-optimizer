import argparse
import asyncio
import json
from dataclasses import asdict
from pathlib import Path

from quantum_link_optimizer.experiments.common import distribution, write_json
from quantum_link_optimizer.experiments.runtime import run_trial
from quantum_link_optimizer.experiments.scenarios import CASES, configuration
from quantum_link_optimizer.selection.config import Settings
from quantum_link_optimizer.telemetry_lab.manifest import (
    begin,
    digest,
    finish,
    verify_files,
)
from quantum_link_optimizer.telemetry_lab.report import records

from .dataset import labelled_rows
from .model import Forecast, evaluate, fit
from .policy import ForecastPolicy


def policies():
    result = [
        {"name": "fixed-a", "forecast": False, "settings": {"mode": "fixed-a"}},
        {"name": "fixed-b", "forecast": False, "settings": {"mode": "fixed-b"}},
    ]

    for forecast in (False, True):
        for copies in (False, True):
            for pacing in (False, True):
                name = "forecast" if forecast else "ewma"
                name += "-copies" if copies else ""
                name += "-paced" if pacing else ""
                result.append(
                    {
                        "name": name,
                        "forecast": forecast,
                        "settings": {
                            "mode": "ewma",
                            "hedge_delay_ms": 40 if copies else None,
                            "pacing_per_s": 5 if pacing else 0,
                            "extra_bytes_total": 12000,
                        },
                    }
                )

    return result


async def compare_live(plan, output, model):
    result = []

    for case in plan["cases"]:
        for seed in plan["validation_seeds"]:
            for policy in policies():
                name = f"{case}-{seed}-{policy['name']}"
                config = configuration(case, seed, count=plan["commands"])
                config["forecast_sha256"] = (
                    digest(output / "model.json") if policy["forecast"] else None
                )
                settings = Settings(
                    min_hold_ms=0, switch_margin_ms=5, **policy["settings"]
                )
                factory = (
                    (lambda settings: ForecastPolicy(settings, model))
                    if policy["forecast"]
                    else None
                )
                path = output / "live-validation" / name
                summary = await run_trial(config, path, settings, factory)
                decisions = [
                    e
                    for e in records(path / "sender.jsonl")
                    if e["event"] == "route_decision"
                ]
                result.append(
                    {
                        "case": case,
                        "seed": seed,
                        "policy": policy["name"],
                        "settings": asdict(settings),
                        "forecast": policy["forecast"],
                        "manifest_sha256": digest(path / "manifest.json"),
                        "relative_path": str(path.relative_to(output)),
                        "decision_ms": distribution(
                            e["decision_elapsed_ns"] / 1e6 for e in decisions
                        ),
                        **summary,
                    }
                )
                print(
                    f"validation {name}: "
                    f"{summary['timely_executed']}/{summary['offered']}",
                    flush=True,
                )

    return result


async def experiment(plan, output):
    seed_groups = [
        set(plan[name]) for name in ("train_seeds", "validation_seeds", "final_seeds")
    ]

    if any(
        left & right
        for i, left in enumerate(seed_groups)
        for right in seed_groups[i + 1 :]
    ):
        raise ValueError("train/validation/final seeds overlap")

    if not all(seed_groups) or not plan["cases"] or set(plan["cases"]) - set(CASES):
        raise ValueError("empty split or unsupported case")

    manifest = begin(output, plan)
    manifest["status"] = "failed"
    split_rows = {"train": [], "validation": []}

    try:
        for split in split_rows:
            for seed in plan[f"{split}_seeds"]:
                for case in plan["cases"]:
                    for path in ("A", "B"):
                        name = f"{split}-{case}-{seed}-{path}"
                        config = configuration(case, seed, count=plan["commands"])
                        directory = output / "development" / name
                        await run_trial(
                            config,
                            directory,
                            Settings(
                                mode=f"fixed-{path.lower()}",
                                min_hold_ms=0,
                                switch_margin_ms=5,
                            ),
                        )

                        if not all(verify_files(directory).values()):
                            raise ValueError("development episode integrity failure")

                        split_rows[split].extend(labelled_rows(directory, name))
                        print(f"collected {name}", flush=True)

        for split, rows in split_rows.items():
            destination = output / f"{split}.jsonl"
            destination.write_text(
                "".join(json.dumps(r, allow_nan=False) + "\n" for r in rows)
            )

        training = split_rows["train"]
        validation = split_rows["validation"]
        model = fit(training, seed=plan["model_seed"], epochs=plan["epochs"])
        model.save(output / "model.json", {r["episode_id"] for r in training})
        model = Forecast.load(output / "model.json")
        quality = evaluate(model, training, validation)
        live = await compare_live(plan, output, model)
        write_json(output / "live-comparison.json", live)
        plain = {(r["case"], r["seed"]): r for r in live if r["policy"] == "ewma"}
        candidate = {
            (r["case"], r["seed"]): r for r in live if r["policy"] == "forecast"
        }
        difference = sum(
            candidate[c]["timely_executed"] - plain[c]["timely_executed"] for c in plain
        )
        report = {
            "schema_version": 1,
            "origin": "uncalibrated laboratory UDP commands and TCP KMS",
            "model_sha256": digest(output / "model.json"),
            "train_rows": len(training),
            "validation_rows": len(validation),
            "train_episodes": len({r["episode_id"] for r in training}),
            "validation_episodes": len({r["episode_id"] for r in validation}),
            "offline_validation": quality,
            "live_validation": live,
            "plain_forecast_minus_ewma_timely_commands": difference,
            "recommend_forecast_for_deployment": False,
            "conclusion": (
                "Validation is exploratory. Deployment requires independently frozen "
                "final trials; validation improvement alone is insufficient."
            ),
            "limitations": [
                "Small independent episode count; rows and A/B pairs are correlated.",
                "The model cannot anticipate an unobserved abrupt fault.",
                (
                    "Inventory polling shares the KMS queue and its cost "
                    "is included for every policy."
                ),
                "No field network, physical QKD, or calibrated clock model.",
            ],
        }
        write_json(output / "forecasting-report.json", report)
        manifest["status"] = "collected"
    finally:
        finish(output, manifest)

    return report


def main():
    parser = argparse.ArgumentParser(
        description="Train-only forecasting and live factorial validation"
    )
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = asyncio.run(experiment(json.loads(args.plan.read_text()), args.output))
    print(
        json.dumps(
            {k: v for k, v in result.items() if k != "live_validation"}, indent=2
        )
    )


if __name__ == "__main__":
    main()
