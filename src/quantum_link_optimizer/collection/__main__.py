import argparse
import asyncio
import json
from pathlib import Path

from .build import build_dataset
from .freeze import freeze
from .plan import episodes
from .series import collect_series


def main():
    parser = argparse.ArgumentParser(description="Paired A/B dataset collection")
    actions = parser.add_subparsers(dest="action", required=True)
    preview = actions.add_parser("plan")
    preview.add_argument("--plan", type=Path, required=True)
    collect = actions.add_parser("collect")
    collect.add_argument("--plan", type=Path, required=True)
    collect.add_argument("--output", type=Path, required=True)
    collect.add_argument("--splits", nargs="+", default=["train", "validation"])
    collect.add_argument("--resume", action="store_true")
    collect.add_argument("--frozen-policy", type=Path)
    build = actions.add_parser("build")
    build.add_argument("--series", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--allow-partial", action="store_true")
    build.add_argument("--preprocessing", type=Path)
    lock = actions.add_parser("freeze")
    lock.add_argument("--dataset", type=Path, required=True)
    lock.add_argument("--policy", type=Path, required=True)
    lock.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.action in {"plan", "collect"}:
        plan = json.loads(args.plan.read_text())
        configs = episodes(plan)
        if args.action == "plan":
            print(
                json.dumps(
                    {
                        "episodes": len(configs),
                        "by_split": {
                            split: sum(config["split"] == split for config in configs)
                            for split in plan["splits"]
                        },
                        "measurement_hours": sum(
                            config["samples"] * config["interval_s"]
                            for config in configs
                        )
                        / 3600,
                        "minimum_episode_s": min(
                            config["samples"] * config["interval_s"]
                            for config in configs
                        ),
                    },
                    indent=2,
                )
            )
        else:
            asyncio.run(
                collect_series(
                    plan, args.output, args.splits, args.resume, args.frozen_policy
                )
            )
    elif args.action == "build":
        print(
            json.dumps(
                build_dataset(
                    args.series, args.output, args.allow_partial, args.preprocessing
                ),
                indent=2,
            )
        )
    else:
        freeze(args.dataset, args.policy, args.output)


if __name__ == "__main__":
    main()
