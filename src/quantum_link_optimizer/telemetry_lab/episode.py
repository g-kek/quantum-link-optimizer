import asyncio
import math
import resource
from contextlib import ExitStack

from quantum_link_optimizer.commands.receiver import Receiver
from quantum_link_optimizer.commands.sender import Sender
from quantum_link_optimizer.commands.session import Session
from quantum_link_optimizer.commands.store import Store
from quantum_link_optimizer.kms.api import Client, close_clients, start_server
from quantum_link_optimizer.kms.model import Settings
from quantum_link_optimizer.kms.service import Service

from .events import EventLog
from .manifest import begin, finish
from .relay import Relay
from .report import summarize


def validate(config):
    if config["split"] not in {"development", "final_test"}:
        raise ValueError("invalid split")
    if config["path"] not in {"A", "B"} or type(config["seed"]) is not int:
        raise ValueError("invalid path or seed")
    for name in ("count", "rotate_after", "queue_limit"):
        if type(config[name]) is not int or config[name] < 1:
            raise ValueError(f"invalid {name}")
    for name in ("interval_s", "deadline_s"):
        if not math.isfinite(config[name]) or config[name] <= 0:
            raise ValueError(f"invalid {name}")
    Settings(**config["kms"])
    for direction, settings in config.get("network", {}).items():
        if direction not in {"forward", "reverse"}:
            raise ValueError("invalid network direction")
        for name, value in settings.items():
            if (
                name not in {"delay_ms", "loss_pct"}
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError("invalid network impairment")
            if name == "loss_pct" and value > 100:
                raise ValueError("invalid loss percentage")
    for event in config.get("events", []):
        if not math.isfinite(event["at_s"]) or event["at_s"] < 0:
            raise ValueError("invalid event time")
        if set(event["state"]) - {"online", "refilling"}:
            raise ValueError("invalid KMS event")
        if any(type(value) is not bool for value in event["state"].values()):
            raise ValueError("KMS state must be boolean")


async def run_episode(config, output):
    validate(config)
    manifest = begin(output, config)
    manifest["topology"] = "loopback TCP KMS, UDP sender-relay-receiver, single process"
    manifest["capture"] = None
    agents = []
    tasks = []
    server = None
    relay = None
    store = None
    failure = None
    with ExitStack() as stack:
        logs = {}
        for name in ("sender", "receiver", "kms", "samples", "truth"):
            logs[name] = EventLog(output / f"{name}.jsonl")
            stack.callback(logs[name].close)
        try:
            service = Service(Settings(**config["kms"]), logs["kms"].emit)
            server = await start_server(service, port=0)
            port = server.sockets[0].getsockname()[1]
            adapter = Client(port=port)
            store = Store(output / "receiver.sqlite")
            receiver = Receiver(
                config["path"],
                adapter,
                store,
                logs["receiver"].emit,
                queue_limit=config["queue_limit"],
                drop_acks=config.get("drop_acks", 0),
            )
            loop = asyncio.get_running_loop()
            transport, _ = await loop.create_datagram_endpoint(
                lambda: receiver, local_addr=("127.0.0.1", 0)
            )
            agents.append(receiver)
            relay = Relay(
                transport.get_extra_info("sockname"),
                config.get("network", {}),
                config["seed"],
            )
            proxy, _ = await loop.create_datagram_endpoint(
                lambda: relay, local_addr=("127.0.0.1", 0)
            )
            session = Session(
                adapter, config["path"], rotate_after=config["rotate_after"]
            )
            sender = Sender(
                session,
                proxy.get_extra_info("sockname"),
                logs["sender"].emit,
                queue_limit=config["queue_limit"],
            )
            await loop.create_datagram_endpoint(
                lambda: sender, local_addr=("127.0.0.1", 0)
            )
            agents.append(sender)
            logs["truth"].emit(
                "initial_conditions",
                network=config.get("network", {}),
                kms=config["kms"],
                drop_acks=config.get("drop_acks", 0),
            )
            started = loop.time()

            async def injections():
                for event in sorted(
                    config.get("events", []), key=lambda item: item["at_s"]
                ):
                    await asyncio.sleep(max(0, started + event["at_s"] - loop.time()))
                    service.control(**event["state"])
                    logs["truth"].emit(
                        "kms_injection",
                        planned_s=event["at_s"],
                        actual_s=loop.time() - started,
                        state=event["state"],
                    )

            async def sample():
                while True:
                    usage = resource.getrusage(resource.RUSAGE_SELF)
                    logs["samples"].emit(
                        "sample",
                        kms={
                            **service.pools.state(),
                            "pending": service.pending,
                            "online": service.online,
                        },
                        process={
                            "user_cpu_s": usage.ru_utime,
                            "system_cpu_s": usage.ru_stime,
                        },
                        queue={
                            "sender": sender.queue.qsize(),
                            "receiver": receiver.queue.qsize(),
                        },
                        qdisc=None,
                        node_load=None,
                        missing={
                            "qdisc": "loopback_no_linux_qdisc",
                            "node_load": "single_process",
                        },
                    )
                    await asyncio.sleep(0.05)

            tasks = [asyncio.create_task(injections()), asyncio.create_task(sample())]
            commands = []
            for sequence in range(config["count"]):
                commands.append(
                    asyncio.create_task(
                        sender.send(sequence, sequence, deadline_s=config["deadline_s"])
                    )
                )
                tasks.append(commands[-1])
                await asyncio.sleep(
                    max(
                        0, started + (sequence + 1) * config["interval_s"] - loop.time()
                    )
                )
            await asyncio.gather(*commands)
            manifest["status"] = "collected"
        except BaseException as error:
            manifest["status"] = "failed"
            manifest["error"] = type(error).__name__
            failure = error
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for agent in reversed(agents):
                await agent.close()
            if relay:
                relay.close()
            if server:
                server.close()
                await server.wait_closed()
                await close_clients(service)
            if store:
                store.close()
    summarize(output)
    finish(output, manifest)
    if failure is not None:
        raise failure
    return manifest
