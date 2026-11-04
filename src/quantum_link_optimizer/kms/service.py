import asyncio
import time

from .model import KmsError, Pools


class Service:
    def __init__(self, settings, emit=None):
        self.pools = Pools(settings)
        self.settings = settings
        self.emit = emit or (lambda event, **fields: None)
        self.pending = 0
        self.worker = asyncio.Lock()
        self.online = True

    def control(self, *, online=None, refilling=None):
        self.pools.refresh()
        if online is not None:
            self.online = online
        if refilling is not None:
            self.pools.refilling = refilling
        self.emit("kms_state", online=self.online, **self.pools.state())

    async def request(self, operation, *, session="", path="A", key_id="", timeout_s=1):
        if not self.online:
            raise KmsError("api_unavailable")
        if self.pending >= self.settings.queue_limit:
            self.emit("kms_rejected", reason="queue_full", path=path, session=session)
            raise KmsError("queue_full")
        started = time.monotonic_ns()
        self.pending += 1
        self.emit(
            "kms_requested",
            operation=operation,
            session=session,
            path=path,
            pending=self.pending,
        )
        try:
            async with asyncio.timeout(timeout_s):
                while True:
                    async with self.worker:
                        await asyncio.sleep(self.settings.delay_s)
                        if not self.online:
                            raise KmsError("api_unavailable")
                        if operation == "state":
                            return {**self.pools.state(), "pending": self.pending}
                        if operation == "get":
                            key = self.pools.get(key_id, session, path)
                        elif operation == "issue":
                            try:
                                key = self.pools.issue(session, path)
                            except KmsError as error:
                                if str(error) != "empty_pool":
                                    raise
                                key = None
                        else:
                            raise KmsError("invalid_operation")
                    if key is not None:
                        self.emit(
                            "kms_delivered",
                            key_id=key.key_id,
                            session=session,
                            path=path,
                            elapsed_ns=time.monotonic_ns() - started,
                            **self.pools.state(),
                        )
                        return key
                    self.emit("kms_wait_pool", session=session, path=path)
                    await asyncio.sleep(0.02)
        except TimeoutError as error:
            self.emit("kms_failed", session=session, path=path, reason="timeout")
            raise KmsError("timeout") from error
        except KmsError as error:
            self.emit("kms_failed", session=session, path=path, reason=str(error))
            raise
        finally:
            self.pending -= 1
