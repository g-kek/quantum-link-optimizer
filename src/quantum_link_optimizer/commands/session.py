import time
import uuid
from typing import Protocol

from quantum_link_optimizer.kms.model import Key


class KeyAdapter(Protocol):
    async def request(self, operation: str, **fields) -> Key: ...


class Session:
    def __init__(self, adapter, path, rotate_after=64, rotate_s=30, session_id=None):
        if path not in {"A", "B"} or rotate_after < 1 or rotate_s <= 0:
            raise ValueError("invalid session policy")
        self.adapter = adapter
        self.path = path
        self.id = session_id or uuid.uuid4().hex
        self.rotate_after = rotate_after
        self.rotate_s = rotate_s
        self.key = None
        self.used = 0
        self.issued_at = 0

    async def acquire(self, timeout_s):
        now = time.monotonic()
        phase = "steady"
        if (
            self.key is None
            or self.used >= self.rotate_after
            or now - self.issued_at >= self.rotate_s
            or now >= self.key.expires_at
        ):
            phase = "startup" if self.key is None else "rotation"
            self.key = await self.adapter.request(
                "issue",
                session=self.id,
                path=self.path,
                timeout_s=timeout_s,
            )
            self.issued_at = time.monotonic()
            self.used = 0
        self.used += 1
        return self.key, phase
