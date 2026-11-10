import argparse
import asyncio
import json
from collections import defaultdict
from pathlib import Path

from quantum_link_optimizer.experiments.common import write_json
from quantum_link_optimizer.experiments.runtime import run_trial
from quantum_link_optimizer.experiments.scenarios import configuration
from quantum_link_optimizer.experiments.validation.metrics import trial_metrics
from quantum_link_optimizer.selection.config import Settings
from quantum_link_optimizer.telemetry_lab.manifest import begin, finish
from quantum_link_optimizer.telemetry_lab.report import records

POLICIES = {
    "fixed-a": ({"mode": "fixed-a"}, {}),
    "fixed-b": ({"mode": "fixed-b"}, {}),
    "ewma-before": ({"mode": "ewma"}, {"prefetch_keys": False}),
    "last": ({"mode": "last"}, {}),
    "ewma": ({"mode": "ewma", "ewma_restart": True}, {}),
    "ewma-copies": ({"mode": "ewma", "hedge_delay_ms": 40}, {}),
    "ewma-risk-copies": (
        {
            "mode": "ewma",
            "hedge_delay_ms": 40,
            "adaptive_hedge": True,
            "hedge_risk": 0.1,
        },
        {},
    ),
}


def ceiling(path, target_ms, overhead_ms=3.0):
    """Commands for which some path answered a probe in time near arrival.

    Probes need no key and no queue, so this is an upper estimate of what
    choosing a path could give, not a promise
    """
    events = records(path / "sender.jsonl")
    probes = defaultdict(list)

    for event in events:
        if event["event"] == "probe_result" and event["status"] == "ok":
            probes[event["path"]].append((event["sent_ns"], event["rtt_ms"]))

    reachable = 0

    for event in events:
        if event["event"] != "workload_arrival":
            continue

        arrival = event["available_ns"] - event["schedule_lag_ns"]
        best = None

        for samples in probes.values():
            after = [rtt for sent, rtt in samples if 0 <= sent - arrival <= 60e6]

            if after:
                best = min(after[0], best) if best is not None else after[0]

        reachable += best is not None and best + overhead_ms <= target_ms

    return reachable


async def collect(plan, output):
    rows = []

    for seed in plan["seeds"]:
        for case in plan["cases"]:
            for name in plan["policies"]:
                overrides, extra = POLICIES[name]
                config = configuration(
                    case, seed, count=plan["commands"], interval_s=plan["interval_s"]
                )
                config.update(extra)
                settings = Settings(
                    min_hold_ms=0,
                    switch_margin_ms=5,
                    shared_segment=case == "shared-queue",
                    extra_bytes_total=12000,
                    **overrides,
                )
                trial = output / "trials" / f"{case}-{seed}-{name}"
                summary = await run_trial(config, trial, settings)
                measured = trial_metrics(trial, summary, case)
                rows.append(
                    {
                        "case": case,
                        "seed": seed,
                        "policy": name,
                        "offered": measured["offered"],
                        "timely": measured["timely_executed"],
                        "extra_path_bytes": measured.get("extra_path_bytes", 0),
                        "network_ceiling": ceiling(trial, config["target_ms"]),
                    }
                )
                print(f"{case}-{seed}-{name}: {rows[-1]['timely']}", flush=True)

    return rows


def summarize(rows, policies):
    cases = sorted({row["case"] for row in rows})
    table = {}

    for case in cases:
        items = [row for row in rows if row["case"] == case]
        offered = sum(row["offered"] for row in items if row["policy"] == policies[0])
        timely = {
            name: sum(row["timely"] for row in items if row["policy"] == name)
            for name in policies
        }
        fixed = max(timely.get("fixed-a", 0), timely.get("fixed-b", 0))
        ceiling_value = max(
            sum(row["network_ceiling"] for row in items if row["policy"] == name)
            for name in policies
        )
        table[case] = {
            "offered": offered,
            "timely": timely,
            "best_fixed": fixed,
            "network_ceiling": ceiling_value,
            "extra_path_bytes": {
                name: sum(
                    row["extra_path_bytes"] for row in items if row["policy"] == name
                )
                for name in policies
            },
        }

    return table


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text())
    manifest = begin(args.output, plan)
    rows = asyncio.run(collect(plan, args.output))
    report = {
        "schema_version": 1,
        "scope": "Laboratory loopback, scheduled delays and declared faults",
        "plan": plan,
        "by_case": summarize(rows, plan["policies"]),
        "trials": rows,
    }
    write_json(args.output / "routing-report.json", report)
    manifest["status"] = "collected"
    finish(args.output, manifest)

    for case, item in report["by_case"].items():
        print(case, item["timely"], "ceiling", item["network_ceiling"])


if __name__ == "__main__":
    main()
