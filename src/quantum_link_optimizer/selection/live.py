import asyncio
import json
import time
import uuid

from quantum_link_optimizer.collection.probes import Echo, Probe
from quantum_link_optimizer.commands.sender import Sender
from quantum_link_optimizer.commands.session import Session
from quantum_link_optimizer.kms.api import Client
from quantum_link_optimizer.telemetry_lab.events import EventLog

from .config import Settings
from .router import Router


async def serve_probes(addresses, ready):
    transports = []
    try:
        for address in addresses:
            transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
                Echo, local_addr=address
            )
            transports.append(transport)
        ready.write_text("ready\n")
        await asyncio.Event().wait()
    finally:
        for transport in transports:
            transport.close()


async def run_sender(config, events):
    journal = EventLog(events, source="sender")
    router = None
    senders, probes, tasks = {}, [], []
    logical_id = uuid.uuid4().hex
    adapter = Client(*config["kms"])

    def emit(event, **fields):
        journal.emit(event, **fields)
        if router:
            router.observe(event, **fields)

    def leg_emit(event, **fields):
        event = {
            "command_submitted": "leg_submitted",
            "command_completed": "leg_completed",
        }.get(event, event)
        emit(event, **fields)

    try:
        settings = Settings(**config["routing"])
        loop = asyncio.get_running_loop()
        for path in ("A", "B"):
            endpoints = config["paths"][path]
            sender = Sender(
                Session(
                    adapter,
                    path,
                    rotate_after=config.get("rotate_after", 64),
                    session_id=logical_id,
                ),
                tuple(endpoints["remote"]),
                leg_emit,
            )
            await loop.create_datagram_endpoint(
                lambda sender=sender: sender, local_addr=tuple(endpoints["local"])
            )
            senders[path] = sender
            probe = Probe(path, tuple(endpoints["probe"]), emit, 2, 64)
            await loop.create_datagram_endpoint(
                lambda probe=probe: probe, local_addr=tuple(endpoints["local"])
            )
            probes.append(probe)
        router = Router(senders, settings, emit, config.get("allow_copy", False))

        async def sample():
            sample_id = 0
            started = time.monotonic()
            while True:
                for probe in probes:
                    probe.send(sample_id)
                for path, sender in senders.items():
                    emit(
                        "sender_queue_sample",
                        path=path,
                        queue_depth=sender.queue.qsize(),
                    )
                sample_id += 1
                await asyncio.sleep(
                    max(
                        0,
                        started
                        + sample_id * config.get("probe_interval_s", 0.1)
                        - time.monotonic(),
                    )
                )

        tasks.append(asyncio.create_task(sample()))
        await asyncio.sleep(config.get("warmup_s", 0.2))
        commands = []
        started = time.monotonic()
        for sequence in range(config["count"]):
            commands.append(
                asyncio.create_task(
                    router.send(
                        sequence,
                        sequence,
                        config["deadline_s"],
                        command_id=f"command-{sequence}",
                    )
                )
            )
            tasks.append(commands[-1])
            await asyncio.sleep(
                max(
                    0,
                    started + (sequence + 1) * config["interval_s"] - time.monotonic(),
                )
            )
        results = await asyncio.gather(*commands)
        print(json.dumps(results))
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if router:
            await router.close()
        for probe in probes:
            probe.close()
        for sender in senders.values():
            await sender.close()
        journal.close()
