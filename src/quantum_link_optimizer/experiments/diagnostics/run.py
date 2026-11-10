import argparse
import asyncio
import json
import re
from pathlib import Path

from quantum_link_optimizer.diagnostics.evaluation import score_trial, summarize
from quantum_link_optimizer.experiments.common import write_json
from quantum_link_optimizer.experiments.runtime import run_trial
from quantum_link_optimizer.experiments.scenarios import configuration
from quantum_link_optimizer.selection.config import Settings
from quantum_link_optimizer.telemetry_lab.manifest import begin, finish


async def collect(plan, output):
    rows = []

    for seed in plan["seeds"]:
        for case in plan["cases"]:
            config = configuration(
                case, seed, count=plan["commands"], interval_s=plan["interval_s"]
            )
            settings = Settings(
                mode="ewma",
                min_hold_ms=0,
                switch_margin_ms=5,
                shared_segment=case == "shared-queue",
            )
            trial = output / "trials" / f"{case}-{seed}"
            await run_trial(config, trial, settings)
            rows.append(score_trial(trial, case))
            print(f"{case}-{seed}: {rows[-1]['false_alarms']} false alarms", flush=True)

    return rows


def rescore(directory):
    """Score earlier trials named <case>-<seed>-ewma with their own journals."""
    rows = []

    for trial in sorted(directory.glob("*-ewma")):
        case = re.match(r"(.+)-\d+-ewma$", trial.name).group(1)
        rows.append(score_trial(trial, case))

    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--baseline", type=Path, help="Final directory of an earlier run to rescore"
    )
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text())
    manifest = begin(args.output, plan)
    rows = asyncio.run(collect(plan, args.output))
    report = {
        "schema_version": 1,
        "scope": "Laboratory scenarios with declared faults, EWMA routing",
        "plan": plan,
        "summary": summarize(rows),
        "trials": rows,
    }

    if args.baseline:
        earlier = rescore(args.baseline)
        report["baseline"] = {
            "directory": str(args.baseline),
            "summary": summarize(earlier),
            "trials": earlier,
        }

    write_json(args.output / "diagnostics-report.json", report)
    manifest["status"] = "collected"
    finish(args.output, manifest)
    print(json.dumps(report["summary"], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
