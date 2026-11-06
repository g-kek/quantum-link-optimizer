import argparse
import asyncio
import json
from pathlib import Path

from .episode import run_episode
from .manifest import verify_files


def main():
    parser = argparse.ArgumentParser(description="Reproducible command episodes")
    actions = parser.add_subparsers(dest="action", required=True)
    run = actions.add_parser("run")
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--final-test", action="store_true")
    verify = actions.add_parser("verify")
    verify.add_argument("--run", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "verify":
        checks = verify_files(args.run)
        print(json.dumps(checks, indent=2))
        if not checks or not all(checks.values()):
            raise SystemExit(1)
        return
    config = json.loads(args.config.read_text())
    if config.get("split") == "final_test" and not args.final_test:
        parser.error("held-out episode requires --final-test; freeze policies first")
    asyncio.run(run_episode(config, args.output))
    print((args.output / "summary.json").read_text())


if __name__ == "__main__":
    main()
