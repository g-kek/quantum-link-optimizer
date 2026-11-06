import asyncio

import pytest

from quantum_link_optimizer.kms.api import Client, start_server
from quantum_link_optimizer.kms.model import KmsError, Pools, Settings
from quantum_link_optimizer.kms.service import Service


@pytest.mark.parametrize("mode,remaining", [("shared", 0), ("separate", 1)])
def test_pool_scope_and_consumption(mode, remaining):
    pools = Pools(Settings(mode=mode, initial=1, capacity=1, refill_per_s=0))
    key = pools.issue("session", "A")
    assert pools.get(key.key_id, "session", "A").material == key.material
    assert pools.available[pools.pool("B")] == remaining
    assert pools.available[pools.pool("A")] == 0
    with pytest.raises(KmsError, match="scope"):
        pools.get(key.key_id, "session", "B")
    with pytest.raises(KmsError, match="unknown"):
        pools.get("invalid", "session", "A")


def test_refill_pause_and_key_expiry():
    now = [0.0]
    pools = Pools(
        Settings(initial=1, capacity=2, refill_per_s=2, key_ttl_s=2),
        clock=lambda: now[0],
    )
    key = pools.issue("session", "A")
    now[0] = 0.5
    assert pools.state()["available"]["shared"] == 1
    pools.refilling = False
    now[0] = 2.5
    assert pools.state()["available"]["shared"] == 1
    with pytest.raises(KmsError, match="unknown"):
        pools.get(key.key_id, "session", "A")


def test_queue_timeout_and_recovery():
    async def exercise():
        service = Service(Settings(initial=0, refill_per_s=0, queue_limit=1))
        waiting = asyncio.create_task(
            service.request("issue", session="s", timeout_s=0.05)
        )
        await asyncio.sleep(0.01)
        with pytest.raises(KmsError, match="queue_full"):
            await service.request("state")
        with pytest.raises(KmsError, match="timeout"):
            await waiting
        assert service.pending == 0
        service.control(online=False)
        with pytest.raises(KmsError, match="unavailable"):
            await service.request("state")
        service.control(online=True)
        assert (await service.request("state"))["pending"] == 1

    asyncio.run(exercise())


def test_two_clients_get_same_key_without_logging_material():
    async def exercise():
        events = []
        service = Service(
            Settings(delay_s=0.01),
            lambda event, **fields: events.append({"event": event, **fields}),
        )
        server = await start_server(service, port=0)
        async with server:
            port = server.sockets[0].getsockname()[1]
            sender, receiver = Client(port=port), Client(port=port)
            key = await sender.request("issue", session="s", path="A")
            matched = await receiver.request(
                "get", key_id=key.key_id, session="s", path="A"
            )
            assert matched.material == key.material
            assert service.pools.state()["available"]["shared"] == 7
            assert all("material" not in event for event in events)
            with pytest.raises(KmsError, match="unknown"):
                await receiver.request("get", key_id="bad", session="s", path="A")

    asyncio.run(exercise())


def test_cancelled_wait_does_not_leak_queue_slot():
    async def exercise():
        service = Service(Settings(initial=0, refill_per_s=0))
        task = asyncio.create_task(service.request("issue", session="s"))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert service.pending == 0

    asyncio.run(exercise())


def test_shutdown_cancels_waiting_api_requests():
    from quantum_link_optimizer.kms.api import close_clients

    async def exercise():
        service = Service(Settings(initial=0, refill_per_s=0))
        server = await start_server(service, port=0)
        client = Client(port=server.sockets[0].getsockname()[1])
        request = asyncio.create_task(client.request("issue", session="s", timeout_s=5))
        try:
            for _ in range(100):
                if service.pending:
                    break
                await asyncio.sleep(0.001)
            assert service.pending == 1
            server.close()
            await server.wait_closed()
            await close_clients(service)
            assert service.pending == 0
            assert not service.connections
            with pytest.raises(KmsError):
                await request
        finally:
            request.cancel()
            await asyncio.gather(request, return_exceptions=True)

    asyncio.run(exercise())
