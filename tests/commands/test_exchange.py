import asyncio
import time

import pytest
from cryptography.exceptions import InvalidTag

from quantum_link_optimizer.commands.receiver import Receiver
from quantum_link_optimizer.commands.sender import Sender
from quantum_link_optimizer.commands.session import Session
from quantum_link_optimizer.commands.store import Store
from quantum_link_optimizer.commands.wire import inspect, seal, unseal
from quantum_link_optimizer.kms.api import Client, start_server
from quantum_link_optimizer.kms.model import Pools, Settings
from quantum_link_optimizer.kms.service import Service


@pytest.mark.parametrize("path", ["A", "B"])
def test_real_udp_ack_retry_and_rotation(tmp_path, path):
    async def exercise():
        events = []

        def emit(event, **fields):
            events.append({"event": event, **fields})

        service = Service(Settings(initial=4, capacity=4, refill_per_s=0))
        server = await start_server(service, port=0)
        adapter = Client(port=server.sockets[0].getsockname()[1])
        store = Store(tmp_path / "state.sqlite")
        receiver = Receiver(path, adapter, store, emit, drop_acks=1)
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: receiver, local_addr=("127.0.0.1", 0)
        )
        sender = Sender(
            Session(adapter, path, rotate_after=2),
            transport.get_extra_info("sockname"),
            emit,
            retry_s=0.02,
        )
        await loop.create_datagram_endpoint(lambda: sender, local_addr=("127.0.0.1", 0))
        try:
            results = [await sender.send(i, i) for i in range(3)]
            assert all(result["status"] == "executed" for result in results)
            assert results[0]["duplicate"]
            assert [result["phase"] for result in results] == [
                "startup",
                "steady",
                "rotation",
            ]
            assert store.db.execute("SELECT executions FROM state").fetchone() == (3,)
            assert service.pools.state()["available"]["shared"] == 2
            assert any(event["event"] == "ack_omitted" for event in events)
        finally:
            await sender.close()
            await receiver.close()
            store.close()
            server.close()
            await server.wait_closed()

    asyncio.run(exercise())


def test_restart_late_duplicate_order_and_conflict(tmp_path):
    path = tmp_path / "state.sqlite"
    body = {
        "id": "id",
        "sequence": 3,
        "value": 42,
        "deadline_ns": time.monotonic_ns() + 1_000_000_000,
    }
    store = Store(path)
    assert store.execute("s", body)["status"] == "executed"
    store.close()
    store = Store(path)
    try:
        assert store.execute("s", body)["duplicate"]
        assert store.execute("s", body | {"value": 43})["status"] == "conflict"
        assert (
            store.execute("s", body | {"id": "old", "sequence": 2})["status"] == "stale"
        )
        assert (
            store.execute("s", body | {"id": "late", "deadline_ns": 0})["status"]
            == "expired"
        )
        assert store.db.execute("SELECT executions FROM state").fetchone() == (1,)
    finally:
        store.close()


def test_authentication_direction_and_wire_budget():
    key = Pools(Settings()).issue("s", "A")
    packet = seal(key, "command", {"id": "test"})
    assert unseal(key, inspect(packet, "command"), "command") == {"id": "test"}
    envelope = inspect(packet, "command")
    envelope["header"]["session"] = "other"
    with pytest.raises(ValueError):
        unseal(key, envelope, "command")
    envelope = inspect(packet, "command")
    envelope["header"]["kind"] = "ack"
    with pytest.raises(InvalidTag):
        unseal(key, envelope, "ack")
    with pytest.raises(ValueError, match="1200"):
        seal(key, "command", {"payload": "x" * 1200})


def test_kms_wait_counts_against_command_deadline(tmp_path):
    async def exercise():
        service = Service(Settings(initial=0, refill_per_s=0))
        sender = Sender(Session(service, "A"), ("127.0.0.1", 1), lambda *a, **k: None)
        await asyncio.get_running_loop().create_datagram_endpoint(
            lambda: sender, local_addr=("127.0.0.1", 0)
        )
        try:
            result = await sender.send(1, 1, deadline_s=0.03)
            assert result["status"] == "kms_error"
            assert sender.pending is None
        finally:
            await sender.close()

    asyncio.run(exercise())


def test_sender_queue_is_bounded_and_no_ack_is_unknown():
    async def exercise():
        service = Service(Settings(delay_s=0.02))
        sender = Sender(
            Session(service, "A"),
            ("127.0.0.1", 9),
            lambda *a, **k: None,
            queue_limit=2,
            retry_s=0.01,
        )
        await asyncio.get_running_loop().create_datagram_endpoint(
            lambda: sender, local_addr=("127.0.0.1", 0)
        )
        try:
            results = await asyncio.gather(
                *(sender.send(i, i, deadline_s=0.05) for i in range(10))
            )
            assert sum(result["status"] == "queue_full" for result in results) >= 7
            uncertain = [result for result in results if result["status"] == "no_ack"]
            assert uncertain and all(
                result["outcome"] == "unknown" for result in uncertain
            )
        finally:
            await sender.close()

    asyncio.run(exercise())
