import argparse
import asyncio
import json
from pathlib import Path

import torch

from quantum_link_optimizer.experiments.common import write_json
from quantum_link_optimizer.experiments.forecasting.model import Forecast
from quantum_link_optimizer.experiments.forecasting.policy import ForecastPolicy
from quantum_link_optimizer.experiments.forecasting.run import policies
from quantum_link_optimizer.experiments.runtime import run_trial
from quantum_link_optimizer.experiments.scenarios import configuration
from quantum_link_optimizer.selection.config import Settings
from quantum_link_optimizer.telemetry_lab.manifest import (
    begin,
    digest,
    finish,
    verify_files,
)

from .benchmark import benchmark
from .freeze import create_lock, read_lock
from .metrics import paired_bootstrap, trial_metrics


async def validate(development, plan, output):
    manifest = begin(output, plan)
    manifest["status"] = "failed"
    trials = []

    try:
        create_lock(development, plan, output / "policy-lock.json")
        lock = read_lock(output / "policy-lock.json")
        plan = lock["plan"]
        frozen_sha = digest(output / "policy-lock.json")
        model = Forecast.load(Path(lock["files"]["model.json"]["path"]))
        torch.set_num_threads(1)
        available = {p["name"]: p for p in policies()}

        if not plan["policies"] or set(plan["policies"]) - available.keys():
            raise ValueError("unknown or empty final policy selection")

        for seed in plan["final_seeds"]:
            for case in plan["cases"]:
                for name in plan["policies"]:
                    policy = available[name]
                    window = plan.get("max_in_flight", 8)
                    config = configuration(
                        case,
                        seed,
                        count=plan["commands"],
                        interval_s=plan["interval_s"],
                        max_in_flight=window,
                    )
                    config["frozen_policy_sha256"] = frozen_sha
                    settings = Settings(
                        min_hold_ms=0,
                        switch_margin_ms=5,
                        shared_segment=case == "shared-queue",
                        max_in_flight=window,
                        **policy["settings"],
                    )
                    factory = (
                        (
                            lambda settings: ForecastPolicy(
                                settings, model, plan["prediction_penalty_ms"]
                            )
                        )
                        if policy["forecast"]
                        else None
                    )
                    relative = f"final/{case}-{seed}-{name}"
                    path = output / relative
                    summary = await run_trial(config, path, settings, factory)

                    if not all(verify_files(path).values()):
                        raise ValueError("final episode integrity failure")

                    measured = trial_metrics(path, summary, case)
                    trials.append(
                        {
                            "case": case,
                            "seed": seed,
                            "policy": name,
                            "relative_path": relative,
                            "manifest_sha256": digest(path / "manifest.json"),
                            **measured,
                        }
                    )
                    print(
                        f"final {case}-{seed}-{name}: "
                        f"{measured['timely_executed']}/{measured['offered']}",
                        flush=True,
                    )

        performance = await benchmark(output / "benchmarks", plan["benchmark"], model)
        comparison = paired_bootstrap(trials)
        bounds = comparison["interval_95"]
        positive_difference = bounds is not None and bounds[0] > 0
        recommend = (
            positive_difference
            and performance["requested_full_workload_budget_status"] == "validated"
        )
        report = {
            "schema_version": 1,
            "scope": (
                "Independent held-out laboratory episodes; not field-network validation"
            ),
            "frozen_policy_sha256": frozen_sha,
            "model_sha256": lock["files"]["model.json"]["sha256"],
            "development_manifest_sha256": lock["files"]["manifest.json"]["sha256"],
            "final_seeds": plan["final_seeds"],
            "trials": trials,
            "paired_forecast_vs_ewma": comparison,
            "heldout_positive_difference": positive_difference,
            "recommend_forecast": recommend,
            "performance": performance,
            "conclusion": (
                "No demonstrated held-out forecast benefit; retain simple rules."
                if not recommend
                else (
                    "Positive descriptive held-out difference; larger replication "
                    "and overhead review remain necessary."
                )
            ),
            "limitations": [
                "Only two replications per scenario family; bootstrap is descriptive.",
                "Co-located simulator, agents and KMS share CPU and event loop.",
                (
                    "Decision metrics exclude event serialization; "
                    "processing spans are a lower bound."
                ),
                (
                    "The 1024-byte application workload is unsupported; "
                    "do not claim its 1 ms budget."
                ),
                "No long-duration physical QKD/PTP/operator experiment was performed.",
            ],
        }
        read_lock(output / "policy-lock.json")
        write_json(output / "validation-report.json", report)
        manifest["status"] = "collected"
    finally:
        finish(output, manifest)

    return report


def main():
    parser = argparse.ArgumentParser(
        description="Frozen independent trials and honest agent budgets"
    )
    parser.add_argument("--development", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(
        validate(args.development, json.loads(args.plan.read_text()), args.output)
    )
    print(
        json.dumps(
            {
                "trials": len(result["trials"]),
                "paired_forecast_vs_ewma": result["paired_forecast_vs_ewma"],
                "recommend_forecast": result["recommend_forecast"],
                "payload_contract": result["performance"]["payload_contract"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
