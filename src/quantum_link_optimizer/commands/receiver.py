import asyncio
import time

from cryptography.exceptions import InvalidTag

from quantum_link_optimizer.kms.model import KmsError

from .identity import logical_session
from .wire import inspect, seal, unseal


class Receiver(asyncio.DatagramProtocol):
    def __init__(self, path, adapter, store, emit, queue_limit=64, drop_acks=0):
        self.path = path
        self.adapter = adapter
        self.store = store
        self.emit = emit
        if queue_limit < 1:
            raise ValueError("queue_limit must be positive")
        self.queue = asyncio.Queue(maxsize=queue_limit)
        self.drop_acks = drop_acks
        self.worker = None
        self.transport = None
        self.cache = {}
        self.fetching = {}
        self.tails = {}

    def connection_made(self, transport):
        self.transport = transport
        self.worker = asyncio.create_task(self.process())

    def datagram_received(self, packet, address):
        try:
            self.queue.put_nowait((packet, address, time.monotonic_ns()))
        except asyncio.QueueFull:
            self.emit("receiver_drop", path=self.path, reason="queue_full")

    async def key(self, key_id, session):
        key = self.cache.get(key_id)

        if key is not None and key.expires_at > time.monotonic():
            return key

        # Packets with the same new key share one request to the KMS
        task = self.fetching.get(key_id)

        if task is None:
            task = asyncio.create_task(
                self.adapter.request(
                    "get", key_id=key_id, session=session, path=self.path
                )
            )
            self.fetching[key_id] = task

        try:
            key = await asyncio.shield(task)
        finally:
            if task.done():
                self.fetching.pop(key_id, None)

        self.cache = {
            item_id: item
            for item_id, item in self.cache.items()
            if item.expires_at > time.monotonic()
        }

        if len(self.cache) >= 128:
            self.cache.pop(next(iter(self.cache)))

        self.cache[key_id] = key
        return key

    def key_id(self, packet):
        try:
            return inspect(packet, "command")["header"]["key_id"]
        except (ValueError, KeyError, TypeError):
            return None

    async def after(self, previous, *item):
        if previous is not None:
            await asyncio.gather(previous, return_exceptions=True)

        await self.handle(*item)

    async def process(self):
        waiting = set()

        try:
            while True:
                packet, address, received = await self.queue.get()
                dequeued = time.monotonic_ns()
                key_id = self.key_id(packet)
                tail = self.tails.get(key_id)
                key = self.cache.get(key_id)
                ready = key is not None and key.expires_at > time.monotonic()

                if key_id is None or (ready and (tail is None or tail.done())):
                    await self.handle(packet, address, received, dequeued)
                    continue

                # A slow key fetch must not hold packets of other keys, packets
                # of the same key keep their order behind it
                task = asyncio.create_task(
                    self.after(tail, packet, address, received, dequeued)
                )
                self.tails[key_id] = task
                waiting.add(task)
                task.add_done_callback(waiting.discard)
        finally:
            for task in waiting:
                task.cancel()

            await asyncio.gather(*waiting, return_exceptions=True)

    async def handle(self, packet, address, received, dequeued):
        try:
            envelope = inspect(packet, "command")
            header = envelope["header"]

            if header["path"] != self.path:
                raise ValueError("wrong path")

            key_started = time.monotonic_ns()
            key_id = header["key_id"]
            key = await self.key(key_id, header["session"])
            key_wait = time.monotonic_ns() - key_started
            processing_started = time.monotonic_ns()
            body = unseal(key, envelope, "command")
            self.emit(
                "command_received",
                command_id=body["id"],
                session=key.session,
                path=self.path,
                received_ns=received,
                key_id=key_id,
                attempt=header["attempt"],
            )
            logical_id = logical_session(key.session, self.path)
            result = self.store.execute(logical_id, body)
            result = {
                **result,
                "receiver_queue_wait_ns": dequeued - received,
                "receiver_key_wait_ns": key_wait,
                "receiver_processing_ns": time.monotonic_ns() - processing_started,
            }
            self.emit(
                "command_result",
                logical_session=logical_id,
                session=key.session,
                path=self.path,
                command_id=body["id"],
                **{k: v for k, v in result.items() if k != "id"},
            )

            if self.drop_acks:
                self.drop_acks -= 1
                self.emit("ack_omitted", command_id=body["id"], path=self.path)
            else:
                ack_processing_started = time.monotonic_ns()
                self.transport.sendto(
                    seal(key, "ack", {**result, "attempt": header["attempt"]}),
                    address,
                )
                self.emit(
                    "ack_sent",
                    receiver_ack_processing_ns=time.monotonic_ns()
                    - ack_processing_started,
                    command_id=body["id"],
                    path=self.path,
                    session=key.session,
                    attempt=header["attempt"],
                )
        except (ValueError, KeyError, TypeError, InvalidTag, KmsError) as error:
            self.emit(
                "receiver_rejected",
                path=self.path,
                reason=str(error)
                if isinstance(error, KmsError)
                else type(error).__name__,
            )
        finally:
            self.queue.task_done()

    async def close(self):
        self.transport.close()
        self.worker.cancel()
        await asyncio.gather(self.worker, return_exceptions=True)
