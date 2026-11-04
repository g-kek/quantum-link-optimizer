import argparse
import asyncio
import json
from pathlib import Path

from .api import close_clients, start_server
from .journal import Journal
from .model import Settings
from .service import Service


async def serve(config, host, port, events):
    journal = Journal(events, source="kms")
    truth = Journal(events.with_name(events.stem + ".truth.jsonl"))
    service = Service(Settings(**config["settings"]), journal.emit)

    async def inject():
        started = asyncio.get_running_loop().time()
        for event in sorted(config.get("events", []), key=lambda item: item["at_s"]):
            await asyncio.sleep(
                max(0, started + event["at_s"] - asyncio.get_running_loop().time())
            )
            service.control(**event["state"])
            truth.emit("kms_injection", planned_s=event["at_s"], state=event["state"])

    async def monitor():
        while True:
            journal.emit(
                "kms_sample",
                online=service.online,
                pending=service.pending,
                **service.pools.state(),
            )
            await asyncio.sleep(0.1)

    task = None
    sampler = None
    try:
        server = await start_server(service, host, port)
        async with server:
            task = asyncio.create_task(inject())
            sampler = asyncio.create_task(monitor())
            await server.serve_forever()
    finally:
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        if sampler:
            sampler.cancel()
            await asyncio.gather(sampler, return_exceptions=True)
        await close_clients(service)
        truth.close()
        journal.close()


def main():
    parser = argparse.ArgumentParser(description="Laboratory KMS with test keys")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9400)
    args = parser.parse_args()
    try:
        asyncio.run(
            serve(
                json.loads(args.config.read_text()), args.host, args.port, args.events
            )
        )
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
