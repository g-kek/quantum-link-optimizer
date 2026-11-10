import asyncio
import time
import uuid
from typing import Protocol

from quantum_link_optimizer.kms.model import Key

from .identity import transport_session


class KeyAdapter(Protocol):
    async def request(self, operation: str, **fields) -> Key: ...


class Session:
    def __init__(
        self,
        adapter,
        path,
        rotate_after=64,
        rotate_s=30,
        session_id=None,
        prefetch=True,
    ):
        if path not in {"A", "B"} or rotate_after < 1 or rotate_s <= 0:
            raise ValueError("invalid session policy")
        self.adapter = adapter
        self.path = path
        self.logical_id = session_id or uuid.uuid4().hex
        self.id = transport_session(self.logical_id, path)
        self.rotate_after = rotate_after
        self.rotate_s = rotate_s
        self.key = None
        self.used = 0
        self.issued_at = 0
        self.lock = asyncio.Lock()
        self.prefetch = prefetch
        self.next = None

    def phase(self):
        now = time.monotonic()
        if self.key is None:
            return "startup"
        if (
            self.used >= self.rotate_after
            or now - self.issued_at >= self.rotate_s
            or now >= self.key.expires_at
        ):
            return "rotation"
        return "steady"

    async def request(self, timeout_s):
        return await self.adapter.request(
            "issue", session=self.id, path=self.path, timeout_s=timeout_s
        )

    async def issue(self, timeout_s):
        task, self.next = self.next, None

        if task is not None:
            try:
                key = await asyncio.wait_for(asyncio.shield(task), timeout_s)

                if key.expires_at > time.monotonic():
                    return key
            except Exception:
                # A failed early request is retried the usual way
                pass

        return await self.request(timeout_s)

    def close(self):
        if self.next is not None:
            self.next.cancel()
            self.next = None

    async def acquire(self, timeout_s):
        # Several commands may be in flight, only one of them rotates the key
        async with asyncio.timeout(timeout_s):
            await self.lock.acquire()

        try:
            phase = self.phase()

            if phase != "steady":
                self.key = await self.issue(timeout_s)
                self.issued_at = time.monotonic()
                self.used = 0

            self.used += 1

            if self.prefetch and self.next is None and self.used >= self.rotate_after:
                self.next = asyncio.create_task(self.request(timeout_s=1.0))

            return self.key, phase
        finally:
            self.lock.release()
