import asyncio
from contextlib import asynccontextmanager

from quantum_link_optimizer.collection.paths import open_path
from quantum_link_optimizer.commands.store import Store
from quantum_link_optimizer.kms.model import Settings as KmsSettings
from quantum_link_optimizer.kms.service import Service
from quantum_link_optimizer.selection.config import Settings
from quantum_link_optimizer.selection.router import Router


@asynccontextmanager
async def laboratory(tmp_path, settings, delay_a=100, delay_b=5, allow_copy=True):
    events = []
    router = None

    def emit(event, **fields):
        names = {
            "command_submitted": "leg_submitted",
            "command_completed": "leg_completed",
        }
        event = names.get(event, event)
        events.append({"event": event, **fields})
        if router:
            router.observe(event, **fields)

    config = {
        "network": {
            "A": {"forward": {"delay_ms": delay_a}, "reverse": {"delay_ms": delay_a}},
            "B": {"forward": {"delay_ms": delay_b}, "reverse": {"delay_ms": delay_b}},
        },
        "seed": 1,
        "queue_limit": 64,
        "probe_limit": 64,
        "probe_timeout_s": 1,
        "rotate_after": 64,
    }
    service = Service(KmsSettings())
    store = Store(tmp_path / "receiver.sqlite")
    resources = {"agents": [], "relays": [], "transports": [], "probes": []}
    try:
        senders = {}
        for path in ("A", "B"):
            senders[path], _, _ = await open_path(
                asyncio.get_running_loop(),
                path,
                config,
                service,
                store,
                emit,
                lambda *a, **k: None,
                resources,
                logical_session="shared",
            )
        router = Router(
            senders,
            settings,
            lambda event, **fields: events.append({"event": event, **fields}),
            allow_copy,
        )
        for path in ("A", "B"):
            router.observe("probe_result", path=path, sample=0, status="ok", rtt_ms=10)
        yield router, store, events
    finally:
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
        store.close()


def test_hedge_wins_once_late_primary_does_not_execute_again(tmp_path):
    async def exercise():
        settings = Settings(
            mode="fixed-a",
            hedge_delay_ms=20,
            max_attempts_per_leg=2,
            extra_bytes_per_command=1200,
            extra_bytes_total=1200,
        )
        async with laboratory(tmp_path, settings) as (router, store, events):
            result = await router.send(42, 1, deadline_s=0.5)
            assert result["status"] == "executed" and result["path"] == "B"
            # This is a delivery/deduplication test, not a real-time CI benchmark.
            # Enforce the command deadline; evaluate the 100 ms SLO in #7.
            assert result["timely"]
            first_ack = next(e for e in events if e["event"] == "ack_received")
            assert first_ack["path"] == "B"
            await asyncio.sleep(0.25)
            assert store.db.execute("SELECT executions FROM state").fetchone() == (1,)
            attempts = [event for event in events if event["event"] == "attempt_sent"]
            assert len([event for event in attempts if event["path"] == "A"]) == 1
            assert (
                sum(event["wire_bytes"] for event in attempts if event["path"] == "B")
                == router.budget.used
                <= 1200
            )

    asyncio.run(exercise())


def test_copy_requires_authorization_and_budget_before_key_issue(tmp_path):
    async def exercise():
        settings = Settings(mode="fixed-a", hedge_delay_ms=10, extra_bytes_total=0)
        async with laboratory(tmp_path, settings, delay_a=20) as (
            router,
            store,
            events,
        ):
            result = await router.send(1, 1, deadline_s=0.2)
            assert result["path"] == "A"
            assert router.senders["B"].session.key is None
            assert router.budget.used == 0
        settings = Settings(mode="fixed-a", hedge_delay_ms=10)
        async with laboratory(tmp_path, settings, delay_a=20) as (
            router,
            store,
            events,
        ):
            result = await router.send(1, 2, deadline_s=0.2, allow_copy=False)
            assert result["path"] == "A"
            assert router.senders["B"].session.key is None

    asyncio.run(exercise())


def test_pacing_deadline_is_not_hidden_from_operation_time(tmp_path):
    async def exercise():
        settings = Settings(mode="fixed-b", pacing_per_s=2)
        async with laboratory(tmp_path, settings) as (router, store, events):
            results = await asyncio.gather(
                router.send(1, 1, deadline_s=0.15), router.send(2, 2, deadline_s=0.15)
            )
            assert results[0]["status"] == "executed"
            assert results[1]["status"] == "pacing_deadline"
            assert results[1]["outcome"] == "not_sent"
            assert store.db.execute("SELECT executions FROM state").fetchone() == (1,)

    asyncio.run(exercise())


def test_cancelled_command_stops_retries_and_worker_remains_usable(tmp_path):
    async def exercise():
        settings = Settings(mode="fixed-a", max_attempts_per_leg=5)
        async with laboratory(tmp_path, settings) as (router, store, events):
            task = asyncio.create_task(router.send(1, 1, deadline_s=1))
            while not any(event["event"] == "attempt_sent" for event in events):
                await asyncio.sleep(0.001)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await asyncio.sleep(0.15)
            assert sum(event["event"] == "attempt_sent" for event in events) == 1
            assert (await router.send(2, 2, deadline_s=1))["status"] == "executed"

    asyncio.run(exercise())


def test_queue_is_bounded_and_lost_ack_keeps_outcome_unknown(tmp_path):
    async def exercise():
        settings = Settings(mode="fixed-a", queue_limit=1, max_attempts_per_leg=1)
        async with laboratory(tmp_path, settings, delay_a=20) as (
            router,
            store,
            events,
        ):
            tasks = [
                asyncio.create_task(router.send(i, i, deadline_s=0.03))
                for i in range(3)
            ]
            results = await asyncio.gather(*tasks)
            assert sum(result["status"] == "queue_full" for result in results) == 2
            assert results[0]["status"] == "no_ack"
            assert results[0]["outcome"] == "unknown"
            await asyncio.sleep(0.1)
            assert store.db.execute("SELECT executions FROM state").fetchone() == (1,)

    asyncio.run(exercise())
