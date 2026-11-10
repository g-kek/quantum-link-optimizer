"""Real UDP/TCP laboratory trials with isolated observation and truth streams."""

import asyncio
import json
import resource
import sys
import time
import uuid
from contextlib import ExitStack
from dataclasses import asdict

from quantum_link_optimizer.collection.paths import open_path
from quantum_link_optimizer.commands.store import Store
from quantum_link_optimizer.incidents.device import Device
from quantum_link_optimizer.kms.api import Client, close_clients, start_server
from quantum_link_optimizer.kms.model import KmsError
from quantum_link_optimizer.kms.model import Settings as KmsSettings
from quantum_link_optimizer.kms.service import Service
from quantum_link_optimizer.selection.config import Settings
from quantum_link_optimizer.selection.router import Router
from quantum_link_optimizer.telemetry_lab.events import EventLog
from quantum_link_optimizer.telemetry_lab.manifest import begin, finish

from .common import write_json
from .faults import restart_receivers
from .forecasting.features import exhaustion_seconds
from .journals import DeferredJournal


async def run_trial(config, output, settings=None, policy_factory=None):
    """Inputs are declared before execution; outcomes never enter enrich()."""
    settings = settings or Settings(min_hold_ms=0, switch_margin_ms=5)
    manifest = begin(output, {**config, "routing": asdict(settings)})
    manifest.update(data_origin="laboratory_packets", backend="loopback")

    resources = {"agents": [], "relays": [], "transports": [], "probes": []}
    tasks = []
    router = None
    service = None
    server = None
    store = None
    pool_observation = None
    key_issues = {"A": 0, "B": 0}
    failure = None
    decision_rows = []
    started_ns = time.monotonic_ns()
    cpu_started = time.process_time()
    bypass = config.get("bypass", False)

    with ExitStack() as stack:
        logs = {}

        for source in ("sender", "receiver", "kms", "samples", "truth"):
            journal_type = (
                DeferredJournal
                if config.get("logging_mode") == "deferred"
                else EventLog
            )
            logs[source] = journal_type(output / f"{source}.jsonl", source=source)
            stack.callback(logs[source].close)

        def emit(event, **fields):
            if event == "session_ready" and fields["phase"] != "steady":
                key_issues[fields["path"]] += 1

            logs["sender"].emit(event, **fields)

            if router is not None:
                router.observe(event, **fields)

        def leg_emit(event, **fields):
            event = {
                "command_submitted": "leg_submitted",
                "command_completed": "leg_completed",
            }.get(event, event)
            emit(event, **fields)

        def enrich(snapshot, body, now):
            elapsed_s = max((now - started_ns) / 1e9, 0.1)
            command_bytes = len(json.dumps(body, separators=(",", ":")).encode())

            for path, row in snapshot.items():
                age = (
                    (now - pool_observation["received_ns"]) / 1e6
                    if pool_observation
                    else None
                )
                fresh = age is not None and 0 <= age <= settings.max_age_ms
                pool = "shared" if config["kms"]["mode"] == "shared" else path
                available = pool_observation["available"].get(pool) if fresh else None
                refill = config["kms"]["refill_per_s"]

                if fresh and not pool_observation["refilling"]:
                    refill = 0

                issues = (
                    sum(key_issues.values()) if pool == "shared" else key_issues[path]
                )
                demand = issues / elapsed_s
                row.update(
                    kms_available_keys=available,
                    kms_demand_per_s=demand,
                    kms_refill_per_s=refill if fresh else None,
                    kms_observation_age_ms=age,
                    command_bytes=command_bytes,
                    context_available_ns=pool_observation["received_ns"]
                    if fresh
                    else None,
                    sender_queue_depth=max(
                        row["sender_queue_depth"] or 0, router.queue.qsize()
                    ),
                )
                row["key_exhaustion_seconds"] = exhaustion_seconds(
                    available, demand, row["kms_refill_per_s"]
                )

                decision_rows.append(
                    {
                        "command_id": body["id"],
                        "path": path,
                        "decision_ns": now,
                        "features": dict(row),
                    }
                )

            return snapshot

        try:
            service = Service(KmsSettings(**config["kms"]), logs["kms"].emit)
            server = await start_server(service, port=0)
            adapter = Client(port=server.sockets[0].getsockname()[1])
            store = Store(output / "receiver.sqlite")
            loop = asyncio.get_running_loop()
            logical_session = uuid.uuid4().hex
            shared_device = (
                Device({"rate_mbit": 0.02, "queue_limit_bytes": 2048})
                if config["case"] == "shared-queue"
                else None
            )
            path_config = {
                **config,
                "rotate_after": config["rotate_after"],
            }
            paths = {}

            for path in ("A", "B"):
                paths[path] = await open_path(
                    loop,
                    path,
                    path_config,
                    adapter,
                    store,
                    emit if bypass else leg_emit,
                    logs["receiver"].emit,
                    resources,
                    device=shared_device,
                    logical_session=logical_session,
                )

            if not bypass:
                router = Router(
                    {path: item[0] for path, item in paths.items()},
                    settings,
                    emit,
                    allow_copy=settings.hedge_delay_ms is not None,
                    policy=policy_factory(settings) if policy_factory else None,
                    enrich=enrich,
                )

            controller = router if router is not None else paths["B"][0]

            async def poll_inventory():
                nonlocal pool_observation

                while True:
                    try:
                        request_started = time.monotonic_ns()
                        state = await adapter.request("state", timeout_s=0.2)
                        received = time.monotonic_ns()
                        pool_observation = {**state, "received_ns": received}
                        emit(
                            "kms_observed",
                            **state,
                            received_ns=received,
                            request_elapsed_ns=received - request_started,
                        )
                    except KmsError as error:
                        emit("kms_observation_missing", reason=str(error))

                    await asyncio.sleep(0.1)

            async def sample():
                sample_id = 0

                while True:
                    for path, (sender, probe, _) in paths.items():
                        probe.send(sample_id)
                        if shared_device is not None:
                            emit(
                                "component_observation",
                                path=path,
                                generated_ns=time.monotonic_ns(),
                                device=shared_device.snapshot(time.monotonic()),
                            )

                        emit(
                            "sender_queue_sample",
                            path=path,
                            queue_depth=sender.queue.qsize(),
                        )

                    # This is ground truth for labels/QA, not a controller input.
                    logs["samples"].emit("pool_truth", **service.pools.state())
                    sample_id += 1
                    await asyncio.sleep(config["probe_interval_s"])

            tasks.extend(
                [
                    asyncio.create_task(poll_inventory()),
                    asyncio.create_task(sample()),
                ]
            )
            await asyncio.sleep(config["warmup_s"])
            command_start = loop.time()
            commands = []

            for sequence in range(config["count"]):
                due = command_start + sequence * config["interval_s"]
                await asyncio.sleep(max(0, due - loop.time()))

                if config["case"] == "swap" and sequence == config["count"] // 2:
                    for path, (_, _, relays) in paths.items():
                        delay = 20 if path == "A" else 80

                        for relay in relays:
                            relay.settings = {
                                direction: {"delay_ms": delay}
                                for direction in ("forward", "reverse")
                            }

                    logs["truth"].emit("fault", cause="path_swap", sequence=sequence)

                for change in config.get("schedule", ()):
                    if change["sequence"] != sequence:
                        continue

                    for relay in paths[change["path"]][2]:
                        relay.settings = {
                            direction: {"delay_ms": change["delay_ms"]}
                            for direction in ("forward", "reverse")
                        }

                    logs["truth"].emit(
                        "fault",
                        cause="scheduled_delay",
                        sequence=sequence,
                        path=change["path"],
                        delay_ms=change["delay_ms"],
                    )

                if (
                    config["case"] == "receiver-restart"
                    and sequence == config["count"] // 2
                ):
                    store, proof = await restart_receivers(
                        resources,
                        store,
                        output,
                        adapter,
                        logs["receiver"].emit,
                        config["queue_limit"],
                    )
                    logs["truth"].emit("receiver_restart", sequence=sequence, **proof)

                emit(
                    "workload_arrival",
                    command_id=f"command-{sequence}",
                    schedule_lag_ns=int(max(0, loop.time() - due) * 1e9),
                )
                commands.append(
                    asyncio.create_task(
                        controller.send(
                            sequence,
                            sequence,
                            config["deadline_s"],
                            command_id=f"command-{sequence}",
                        )
                    )
                )

            results = await asyncio.gather(*commands)
            await asyncio.sleep(config["key_horizon_s"] + 0.05)
            manifest["status"] = "collected"
            write_json(output / "results.json", results)
            write_json(output / "decision-features.json", decision_rows)
        except BaseException as error:
            manifest.update(status="failed", error=type(error).__name__)
            failure = error
        finally:
            for task in tasks:
                task.cancel()

            await asyncio.gather(*tasks, return_exceptions=True)

            if router is not None:
                await router.close()

            for probe in resources["probes"]:
                probe.close()

            for agent in reversed(resources["agents"]):
                await agent.close()

            for relay in resources["relays"]:
                relay.close()

            for transport in resources["transports"]:
                transport.close()

            if server is not None:
                server.close()
                await server.wait_closed()
                await close_clients(service)

            if store is not None:
                store.close()

    from quantum_link_optimizer.selection.report import report

    summary = report(output, settings.target_ms)
    write_json(
        output / "resource-usage.json",
        {
            "process_cpu_s": time.process_time() - cpu_started,
            "wall_s": (time.monotonic_ns() - started_ns) / 1e9,
            "process_high_water_rss_bytes": resource.getrusage(
                resource.RUSAGE_SELF
            ).ru_maxrss
            * (1 if sys.platform == "darwin" else 1024),
            "scope": (
                "Whole co-located trial, including sender/receiver/KMS/relay/log "
                "export;"
                "not isolated agent CPU"
            ),
        },
    )
    finish(output, manifest)

    if failure is not None:
        raise failure

    return summary
