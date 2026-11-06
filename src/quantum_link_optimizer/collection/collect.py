import asyncio
import uuid
from contextlib import ExitStack

from quantum_link_optimizer.commands.store import Store
from quantum_link_optimizer.incidents.runtime import Runtime
from quantum_link_optimizer.kms.api import Client, close_clients, start_server
from quantum_link_optimizer.kms.model import Settings
from quantum_link_optimizer.kms.service import Service
from quantum_link_optimizer.telemetry_lab.events import EventLog
from quantum_link_optimizer.telemetry_lab.manifest import begin, finish

from .paths import open_path


async def collect_episode(config, output):
    manifest = begin(output, config)
    manifest.update(
        episode_id=config["episode_id"],
        backend="loopback",
        topology="parallel A/B UDP probes and AEAD commands",
        data_origin="laboratory_packets",
        capture=None,
        assumptions=config.get("incidents", {}).get("assumptions", []),
    )
    resources = {"agents": [], "relays": [], "transports": [], "probes": []}
    tasks = []
    server = None
    service = None
    store = None
    failure = None
    incidents = None
    router = None
    logical_session = uuid.uuid4().hex
    with ExitStack() as stack:
        logs = {}
        for name in ("sender", "receiver", "kms", "samples", "truth"):
            logs[name] = EventLog(output / f"{name}.jsonl")
            stack.callback(logs[name].close)
        command_samples = {}

        def emit(event, **fields):
            if fields.get("command_id") in command_samples:
                fields["sample"] = command_samples[fields["command_id"]]
            logs["sender"].emit(event, **fields)
            if router:
                router.observe(event, **fields)

        def leg_emit(event, **fields):
            names = {
                "command_submitted": "leg_submitted",
                "command_completed": "leg_completed",
            }
            emit(names.get(event, event), **fields)

        try:
            service = Service(Settings(**config["kms"]), logs["kms"].emit)
            server = await start_server(service, port=0)
            adapter = Client(port=server.sockets[0].getsockname()[1])
            store = Store(output / "receiver.sqlite")
            loop = asyncio.get_running_loop()
            if "incidents" in config:
                incidents = Runtime(
                    config["incidents"],
                    config["samples"],
                    config["seed"],
                    emit,
                    logs["truth"].emit,
                )
            paths = {}
            for path in ("A", "B"):
                paths[path] = await open_path(
                    loop,
                    path,
                    config,
                    adapter,
                    store,
                    leg_emit if "routing" in config else emit,
                    logs["receiver"].emit,
                    resources,
                    device=incidents.devices[path] if incidents else None,
                    logical_session=logical_session,
                )
            if "routing" in config:
                from quantum_link_optimizer.selection.config import (
                    Settings as RoutingSettings,
                )
                from quantum_link_optimizer.selection.router import Router

                router = Router(
                    {path: item[0] for path, item in paths.items()},
                    RoutingSettings(**config["routing"]),
                    emit,
                    allow_copy=config.get("allow_copy", False),
                )
            logs["truth"].emit(
                "initial_conditions", paths=config["network"], profile=config["profile"]
            )
            changes = {
                event["sample"]: event["paths"]
                for event in config.get("network_events", [])
            }
            started = loop.time()
            for sample in range(config["samples"]):
                await asyncio.sleep(
                    max(0, started + sample * config["interval_s"] - loop.time())
                )
                if incidents:
                    incidents.tick(sample)
                if sample in changes:
                    for path, settings in changes[sample].items():
                        for relay in paths[path][2]:
                            relay.settings = settings
                    logs["truth"].emit(
                        "network_change", sample=sample, paths=changes[sample]
                    )
                for path in ("A", "B"):
                    emit(
                        "sender_queue_sample",
                        path=path,
                        queue_depth=paths[path][0].queue.qsize(),
                    )
                command_due = (
                    sample >= config.get("warmup_samples", 0)
                    and sample % config["command_every"] == 0
                )
                selected = (
                    config["fixed_path"] if command_due and router is None else None
                )
                emit("decision", sample=sample, selected_path=selected)
                for path in ("A", "B"):
                    paths[path][1].send(sample)
                if command_due:
                    command_id = f"command-{sample}"
                    command_samples[command_id] = sample
                    sender = router if router else paths[selected][0]
                    tasks.append(
                        asyncio.create_task(
                            sender.send(
                                sample,
                                sample,
                                config["command_deadline_s"],
                                command_id=command_id,
                            )
                        )
                    )
                logs["samples"].emit(
                    "kms_sample",
                    **service.pools.state(),
                    pending=service.pending,
                    qdisc=None,
                    missing={"qdisc": "loopback_no_linux_qdisc"},
                )
            await asyncio.gather(*tasks)
            while any(probe.pending for probe in resources["probes"]):
                await asyncio.sleep(0.01)
            manifest["status"] = "collected"
        except BaseException as error:
            manifest["status"] = "failed"
            manifest["error"] = type(error).__name__
            failure = error
        finally:
            if incidents:
                incidents.close()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if router:
                await router.close()
            for probe in resources["probes"]:
                probe.close()
            for agent in reversed(resources["agents"]):
                await agent.close()
            for relay in resources["relays"]:
                relay.close()
            for transport in resources["transports"]:
                transport.close()
            if server:
                server.close()
                await server.wait_closed()
                await close_clients(service)
            if store:
                store.close()
    if router and manifest["status"] == "collected":
        from quantum_link_optimizer.selection.report import report

        report(output, router.settings.target_ms)
    finish(output, manifest)
    if failure:
        raise failure
    return manifest
