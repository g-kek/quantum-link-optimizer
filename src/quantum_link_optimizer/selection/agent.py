import argparse
import asyncio
import json
from pathlib import Path

from quantum_link_optimizer.commands.__main__ import address

from .live import run_sender, serve_probes


def main():
    parser = argparse.ArgumentParser(
        description="Online A/B agent for Linux namespaces"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    send = commands.add_parser("send")
    send.add_argument("--config", type=Path, required=True)
    send.add_argument("--events", type=Path, required=True)
    probes = commands.add_parser("probes")
    probes.add_argument("--a", required=True)
    probes.add_argument("--b", required=True)
    probes.add_argument("--ready", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "send":
            asyncio.run(run_sender(json.loads(args.config.read_text()), args.events))
        else:
            asyncio.run(serve_probes([address(args.a), address(args.b)], args.ready))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
