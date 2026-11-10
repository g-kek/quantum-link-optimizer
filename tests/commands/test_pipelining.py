import asyncio
import time

from quantum_link_optimizer.collection.paths import open_path
from quantum_link_optimizer.commands.store import Store
from quantum_link_optimizer.kms.model import Settings
from quantum_link_optimizer.kms.service import Service


async def one_path(
    tmp_path, service, delay_ms, max_in_flight, rotate_after=64, prefetch=True
):
    config = {
        "network": {
            "A": {
                "forward": {"delay_ms": delay_ms},
                "reverse": {"delay_ms": delay_ms},
            }
        },
        "seed": 1,
        "queue_limit": 64,
        "probe_limit": 64,
        "probe_timeout_s": 1,
        "rotate_after": rotate_after,
        "max_in_flight": max_in_flight,
        "prefetch_keys": prefetch,
    }
    store = Store(tmp_path / f"state-{max_in_flight}-{prefetch}.sqlite")
    resources = {"agents": [], "relays": [], "transports": [], "probes": []}
    sender, _, _ = await open_path(
        asyncio.get_running_loop(),
        "A",
        config,
        service,
        store,
        lambda *args, **fields: None,
        lambda *args, **fields: None,
        resources,
    )
    return sender, store, resources


async def close(store, resources):
    for probe in resources["probes"]:
        probe.close()

    for agent in reversed(resources["agents"]):
        await agent.close()

    for relay in resources["relays"]:
        relay.close()

    for transport in resources["transports"]:
        transport.close()

    store.close()


async def send_burst(sender, count):
    started = time.monotonic()
    results = await asyncio.gather(
        *(sender.send(i, i, deadline_s=2) for i in range(count))
    )
    return results, time.monotonic() - started


def test_sender_does_not_wait_for_previous_ack(tmp_path):
    async def exercise():
        service = Service(Settings())
        elapsed = {}

        for window in (1, 8):
            sender, store, resources = await one_path(tmp_path, service, 50, window)

            try:
                results, elapsed[window] = await send_burst(sender, 6)
                assert [r["status"] for r in results] == ["executed"] * 6
            finally:
                await close(store, resources)

        # Six commands over a 100 ms RTT: one after another vs all at once
        assert elapsed[1] > 0.55
        assert elapsed[8] < 0.35

    asyncio.run(exercise())


def test_concurrent_commands_share_one_key_rotation(tmp_path):
    async def exercise(prefetch):
        service = Service(Settings(initial=10, capacity=10, refill_per_s=0))
        sender, store, resources = await one_path(
            tmp_path, service, 5, 8, rotate_after=2, prefetch=prefetch
        )

        try:
            results, _ = await send_burst(sender, 6)
            assert [r["status"] for r in results] == ["executed"] * 6
            return service.pools.state()["available"]["shared"]
        finally:
            await close(store, resources)

    # Six commands need three keys, prefetch also holds the next one ready
    assert asyncio.run(exercise(prefetch=False)) == 7
    assert asyncio.run(exercise(prefetch=True)) == 6


def test_receiver_keeps_order_while_a_key_is_fetched(tmp_path):
    async def exercise():
        service = Service(Settings(delay_s=0.03))
        sender, store, resources = await one_path(
            tmp_path, service, 5, 8, rotate_after=2
        )

        try:
            results, _ = await send_burst(sender, 6)
            assert [r["status"] for r in results] == ["executed"] * 6
            assert store.db.execute("SELECT value FROM state").fetchone() == (5,)
        finally:
            await close(store, resources)

    asyncio.run(exercise())
