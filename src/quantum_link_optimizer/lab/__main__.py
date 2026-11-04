import argparse
import os
import platform
import shutil
from pathlib import Path

from .config import load_config
from .runner import run_scenario


def main():
    parser = argparse.ArgumentParser(description="Two-path Linux packet laboratory")
    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("doctor")
    launch = subparsers.add_parser("run")
    launch.add_argument("--config", type=Path, required=True)
    launch.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if platform.system() != "Linux":
        parser.error("Linux is required; on macOS run inside a Linux VM")
    missing = [
        tool
        for tool in ("ip", "tc", "sysctl", "ping", "iperf3", "tcpdump")
        if not shutil.which(tool)
    ]
    if missing:
        parser.error("missing tools: " + ", ".join(missing))
    if os.geteuid() != 0:
        parser.error("root is required inside the Linux guest")
    if args.action == "doctor":
        print("Linux, root and executables: OK. Run baseline to check kernel support.")
        return
    config = load_config(args.config)
    run_scenario(config, args.output)
    print(
        f"Collected: {args.output}. "
        "Inspect captures and queues; effects are not auto-verified."
    )


if __name__ == "__main__":
    main()
