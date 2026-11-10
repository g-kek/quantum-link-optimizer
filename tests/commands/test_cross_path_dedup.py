import asyncio
from concurrent.futures import ThreadPoolExecutor

from quantum_link_optimizer.commands.message import create, fingerprint
from quantum_link_optimizer.commands.receiver import Receiver
from quantum_link_optimizer.commands.sender import Sender
from quantum_link_optimizer.commands.session import Session
from quantum_link_optimizer.commands.store import Store
from quantum_link_optimizer.commands.wire import seal
from quantum_link_optimizer.kms.model import Settings
from quantum_link_optimizer.kms.service import Service


def test_two_crypto_sessions_one_execution_and_restart(tmp_path):
    async def exercise():
        service = Service(Settings())
        body = create(42, 1, deadline_s=2, command_id="same-command")
        database = tmp_path / "receiver.sqlite"
        sessions = {
            path: Session(service, path, session_id="logical-session")
            for path in ("A", "B")
        }
        assert sessions["A"].id != sessions["B"].id
        for restarting in (False, True):
            agents, stores, senders = [], [], []
            try:
                for path in ("A", "B"):
                    store = Store(database)
                    stores.append(store)
                    receiver = Receiver(path, service, store, lambda *a, **k: None)
                    loop = asyncio.get_running_loop()
                    transport, _ = await loop.create_datagram_endpoint(
                        lambda receiver=receiver: receiver, local_addr=("127.0.0.1", 0)
                    )
                    agents.append(receiver)
                    sender = Sender(
                        sessions[path],
                        transport.get_extra_info("sockname"),
                        lambda *a, **k: None,
                    )
                    await loop.create_datagram_endpoint(
                        lambda sender=sender: sender, local_addr=("127.0.0.1", 0)
                    )
                    agents.append(sender)
                    senders.append(sender)
                results = await asyncio.gather(
                    *(sender.submit(body) for sender in senders)
                )
                assert all(result["status"] == "executed" for result in results)
                assert sum(result["duplicate"] for result in results) == (
                    2 if restarting else 1
                )
                assert stores[0].db.execute(
                    "SELECT executions FROM state"
                ).fetchone() == (1,)
            finally:
                for agent in reversed(agents):
                    await agent.close()
                for store in stores:
                    store.close()

    asyncio.run(exercise())


def test_concurrent_database_connections_serialize_deduplication(tmp_path):
    database = tmp_path / "receiver.sqlite"
    Store(database).close()
    body = create(42, 1, deadline_s=5)

    def execute(_):
        store = Store(database)
        try:
            return store.execute("logical", body)
        finally:
            store.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(execute, range(2)))
    assert sum(result["duplicate"] for result in results) == 1
    store = Store(database)
    try:
        assert store.db.execute("SELECT executions FROM state").fetchone() == (1,)
    finally:
        store.close()


def test_ack_for_reused_id_with_different_body_is_not_accepted():
    async def exercise():
        service = Service(Settings())
        session = Session(service, "A")
        key, _ = await session.acquire(1)
        sender = Sender(session, ("127.0.0.1", 1), lambda *a, **k: None)
        old = create(1, 1, command_id="reused")
        current = create(2, 1, command_id="reused")
        future = asyncio.get_running_loop().create_future()
        sender.pending = {current["id"]: (key, current, future)}
        result = {
            "id": "reused",
            "status": "executed",
            "request_hash": fingerprint(old),
        }
        sender.datagram_received(seal(key, "ack", result), sender.remote)
        assert not future.done()
        result["request_hash"] = fingerprint(current)
        sender.datagram_received(seal(key, "ack", result), sender.remote)
        assert future.result()["status"] == "executed"

    asyncio.run(exercise())
