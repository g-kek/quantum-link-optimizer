"""Post-deployment laboratory faults; controllers receive no fault IDs."""

import asyncio

from quantum_link_optimizer.commands.receiver import Receiver
from quantum_link_optimizer.commands.store import Store


async def restart_receivers(resources, store, output, adapter, emit, queue_limit):
    receivers = [agent for agent in resources["agents"] if isinstance(agent, Receiver)]
    endpoints = [
        (receiver.path, receiver.transport.get_extra_info("sockname"))
        for receiver in receivers
    ]
    before = store.db.execute(
        "SELECT COALESCE(SUM(executions), 0) FROM state"
    ).fetchone()[0]

    for receiver in receivers:
        await receiver.close()
        resources["agents"].remove(receiver)

    store.close()
    reopened = Store(output / "receiver.sqlite")
    after = reopened.db.execute(
        "SELECT COALESCE(SUM(executions), 0) FROM state"
    ).fetchone()[0]
    loop = asyncio.get_running_loop()

    for path, address in endpoints:
        receiver = Receiver(path, adapter, reopened, emit, queue_limit=queue_limit)
        await loop.create_datagram_endpoint(
            lambda receiver=receiver: receiver, local_addr=address
        )
        resources["agents"].append(receiver)

    return reopened, {"executions_before": before, "executions_after": after}
