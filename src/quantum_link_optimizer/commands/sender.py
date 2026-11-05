import asyncio
import time

from cryptography.exceptions import InvalidTag

from quantum_link_optimizer.kms.model import KmsError

from .message import create, fingerprint, validate
from .wire import inspect, seal, unseal


class Sender(asyncio.DatagramProtocol):
    def __init__(self, session, remote, emit, queue_limit=64, retry_s=0.1):
        self.session = session
        self.remote = remote
        self.emit = emit
        if queue_limit < 1:
            raise ValueError("queue_limit must be positive")
        self.queue = asyncio.Queue(maxsize=queue_limit)
        self.retry_s = retry_s
        self.pending = None
        self.active_future = None
        self.active_work = None
        self.worker = None
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport
        self.worker = asyncio.create_task(self.process())

    def datagram_received(self, packet, address):
        if self.pending is None or address != self.remote:
            return
        key, body, future = self.pending
        try:
            result = unseal(key, inspect(packet, "ack"), "ack")
            if (
                result["id"] != body["id"]
                or result["request_hash"] != fingerprint(body)
                or future.done()
            ):
                return
            future.set_result(result)
        except (ValueError, KeyError, TypeError, InvalidTag):
            self.emit("ack_rejected", path=self.session.path)

    async def send(self, value, sequence, deadline_s=1, command_id=None):
        started = time.monotonic_ns()
        return await self.submit(
            create(value, sequence, deadline_s, command_id), started
        )

    async def submit(self, body, started_ns=None, reserve=None, max_attempts=None):
        validate(body)
        body = dict(body)
        started = started_ns if started_ns is not None else time.monotonic_ns()
        future = asyncio.get_running_loop().create_future()
        self.emit(
            "command_submitted",
            command_id=body["id"],
            session=self.session.id,
            path=self.session.path,
            deadline_ns=body["deadline_ns"],
        )
        try:
            self.queue.put_nowait((body, started, future, reserve, max_attempts))
        except asyncio.QueueFull:
            self.emit(
                "command_completed",
                command_id=body["id"],
                status="queue_full",
                path=self.session.path,
                session=self.session.id,
            )
            return {"id": body["id"], "status": "queue_full"}
        try:
            return await future
        except asyncio.CancelledError:
            if self.active_future is future and self.active_work:
                work = self.active_work
                work.cancel()
                await asyncio.gather(work, return_exceptions=True)
            raise

    async def process(self):
        while True:
            body, started, future, reserve, max_attempts = await self.queue.get()
            work = None

            cancel_work = None
            try:
                if future.cancelled():
                    continue
                work = asyncio.create_task(self.execute(body, reserve, max_attempts))
                self.active_future, self.active_work = future, work

                def cancel_work(completed, work=work):
                    if completed.cancelled():
                        work.cancel()

                future.add_done_callback(cancel_work)
                result = await work
                self.emit(
                    "command_completed",
                    command_id=body["id"],
                    session=self.session.id,
                    path=self.session.path,
                    elapsed_ns=time.monotonic_ns() - started,
                    **result,
                )
                if not future.done():
                    future.set_result({"id": body["id"], **result})
            except (ValueError, KmsError, TimeoutError) as error:
                status = (
                    "kms_error"
                    if isinstance(error, KmsError)
                    else "invalid_command"
                    if isinstance(error, ValueError)
                    else "timeout"
                )
                self.emit(
                    "command_completed",
                    command_id=body["id"],
                    status=status,
                    reason=str(error)
                    if isinstance(error, KmsError)
                    else type(error).__name__,
                    session=self.session.id,
                    path=self.session.path,
                    elapsed_ns=time.monotonic_ns() - started,
                )
                if not future.done():
                    future.set_result({"id": body["id"], "status": status})
            except asyncio.CancelledError:
                future.cancel()
                if asyncio.current_task().cancelling():
                    raise
                self.emit(
                    "leg_abandoned", command_id=body["id"], path=self.session.path
                )

            finally:
                if cancel_work:
                    future.remove_done_callback(cancel_work)
                self.active_future, self.active_work = None, None
                self.pending = None
                self.queue.task_done()

    async def execute(self, body, reserve=None, max_attempts=None):
        remaining = (body["deadline_ns"] - time.monotonic_ns()) / 1e9
        if remaining <= 0:
            return {"status": "expired_before_send"}
        key_started = time.monotonic_ns()
        self.emit(
            "key_wait_started",
            session=self.session.id,
            path=self.session.path,
            command_id=body["id"],
            phase=self.session.phase(),
        )
        key, phase = await self.session.acquire(remaining)
        self.emit(
            "session_ready",
            session=self.session.id,
            path=self.session.path,
            command_id=body["id"],
            phase=phase,
            key_id=key.key_id,
            key_wait_ns=time.monotonic_ns() - key_started,
        )
        ack = asyncio.get_running_loop().create_future()
        self.pending = key, body, ack
        attempt = 0
        while (remaining := (body["deadline_ns"] - time.monotonic_ns()) / 1e9) > 0:
            can_send = max_attempts is None or attempt < max_attempts
            if can_send:
                packet = seal(key, "command", body, attempt=attempt + 1)
                can_send = reserve is None or reserve(len(packet) + 28)
            if can_send:
                attempt += 1
                self.transport.sendto(packet, self.remote)
                sent = time.monotonic_ns()
                self.emit(
                    "attempt_sent",
                    command_id=body["id"],
                    session=self.session.id,
                    path=self.session.path,
                    attempt=attempt,
                    key_id=key.key_id,
                    sent_ns=sent,
                    wire_bytes=len(packet) + 28,
                )
            elif attempt == 0:
                return {
                    "status": "budget_rejected",
                    "outcome": "not_sent",
                    "phase": phase,
                }
            wait_s = min(remaining, self.retry_s) if can_send else remaining
            try:
                result = await asyncio.wait_for(asyncio.shield(ack), wait_s)
                received = time.monotonic_ns()
                self.emit(
                    "ack_received",
                    receiver_queue_wait_ns=result.get("receiver_queue_wait_ns"),
                    receiver_key_wait_ns=result.get("receiver_key_wait_ns"),
                    receiver_processing_ns=result.get("receiver_processing_ns"),
                    command_id=body["id"],
                    session=self.session.id,
                    path=self.session.path,
                    attempt=attempt,
                    rtt_ns=received - sent
                    if result.get("attempt") == attempt
                    else None,
                )
                return {k: v for k, v in result.items() if k != "id"} | {
                    "timely": received <= body["deadline_ns"],
                    "phase": phase,
                }
            except TimeoutError:
                continue
        return {"status": "no_ack", "phase": phase, "outcome": "unknown"}

    async def close(self):
        self.transport.close()
        self.worker.cancel()
        await asyncio.gather(self.worker, return_exceptions=True)
        while not self.queue.empty():
            _, _, future, _, _ = self.queue.get_nowait()
            future.cancel()
            self.queue.task_done()
