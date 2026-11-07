"""A separate declared queue-pressure diagnostic, not policy tuning on final."""

import argparse
import asyncio
import json
from pathlib import Path

from quantum_link_optimizer.experiments.common import write_json
from quantum_link_optimizer.experiments.runtime import run_trial
from quantum_link_optimizer.experiments.scenarios import configuration
from quantum_link_optimizer.selection.config import Settings
from quantum_link_optimizer.telemetry_lab.manifest import begin, finish

from .metrics import trial_metrics


async def stress(plan, output):
    manifest = begin(output, plan)
    manifest["status"] = "failed"
    rows = []

    try:
        for logging in ("sync", "deferred"):
            config = configuration(
                "normal",
                plan["seed"],
                count=plan["messages"],
                interval_s=1 / plan["rate_per_s"],
            )
            config.update(logging_mode=logging, deadline_s=0.2, rotate_after=10000)
            config["kms"].update(initial=10000, capacity=10000, refill_per_s=0)

            for path in config["network"].values():
                for direction in path.values():
                    direction["delay_ms"] = plan["delay_per_direction_ms"]

            settings = Settings(
                mode="ewma", min_hold_ms=0, queue_limit=plan["queue_limit"]
            )
            path = output / logging
            summary = await run_trial(config, path, settings)
            measured = trial_metrics(path, summary, "normal")

            if measured["statuses"].get("queue_full", 0) == 0:
                raise ValueError(
                    "pressure diagnostic did not exercise admission rejection"
                )

            if measured["offered"] != plan["messages"]:
                raise ValueError("offered arrivals disappeared")

            rows.append({"logging_mode": logging, **measured})
            print(f"pressure {logging}: {measured['statuses']}", flush=True)

        report = {
            "schema_version": 1,
            "plan": plan,
            "trials": rows,
            "scope": (
                "Separate declared queue-pressure diagnostic after primary "
                "final collection; no model training or policy selection"
            ),
        }
        write_json(output / "stress-report.json", report)
        manifest["status"] = "collected"
    finally:
        finish(output, manifest)

    return report


def main():
    parser = argparse.ArgumentParser(
        description="Bounded-queue open-loop pressure diagnostic"
    )
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    asyncio.run(stress(json.loads(args.plan.read_text()), args.output))


if __name__ == "__main__":
    main()
