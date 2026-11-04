import asyncio
from contextlib import ExitStack

from quantum_link_optimizer.commands.store import Store
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
        topology="parallel A/B UDP probes and fixed-path AEAD commands",
        data_origin="laboratory_packets",
        capture=None,
    )
    resources = {"agents": [], "relays": [], "transports": [], "probes": []}
    tasks = []
    server = None
    service = None
    store = None
    failure = None
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

        try:
            service = Service(Settings(**config["kms"]), logs["kms"].emit)
            server = await start_server(service, port=0)
            adapter = Client(port=server.sockets[0].getsockname()[1])
            store = Store(output / "receiver.sqlite")
            loop = asyncio.get_running_loop()
            paths = {}
            for path in ("A", "B"):
                paths[path] = await open_path(
                    loop,
                    path,
                    config,
                    adapter,
                    store,
                    emit,
                    logs["receiver"].emit,
                    resources,
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
                selected = (
                    config["fixed_path"]
                    if sample % config["command_every"] == 0
                    else None
                )
                emit("decision", sample=sample, selected_path=selected)
                for path in ("A", "B"):
                    paths[path][1].send(sample)
                if selected:
                    command_id = f"command-{sample}"
                    command_samples[command_id] = sample
                    tasks.append(
                        asyncio.create_task(
                            paths[selected][0].send(
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
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
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
    finish(output, manifest)
    if failure:
        raise failure
    return manifest
