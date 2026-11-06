import math
import secrets
import time
from dataclasses import dataclass, field


class KmsError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    mode: str = "shared"
    capacity: int = 8
    initial: int = 8
    refill_per_s: float = 1
    delay_s: float = 0
    queue_limit: int = 16
    key_ttl_s: float = 120
    retained_limit: int = 4096

    def __post_init__(self):
        if self.mode not in {"shared", "separate"}:
            raise ValueError("mode must be shared or separate")
        for name in ("capacity", "initial", "queue_limit", "retained_limit"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"invalid {name}")
        if (
            self.initial > self.capacity
            or not self.queue_limit
            or not self.retained_limit
        ):
            raise ValueError("invalid pool or queue limits")
        for name in ("refill_per_s", "delay_s", "key_ttl_s"):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"invalid {name}")
        if not self.key_ttl_s:
            raise ValueError("key TTL must be positive")


@dataclass(repr=False)
class Key:
    key_id: str
    session: str
    path: str
    expires_at: float
    material: bytes = field(repr=False)


class Pools:
    def __init__(self, settings, clock=time.monotonic):
        self.settings = settings
        self.clock = clock
        self.available = dict.fromkeys(
            ["shared"] if settings.mode == "shared" else ["A", "B"], settings.initial
        )
        self.updated = clock()
        self.refilling = True
        self.keys = {}

    def pool(self, path):
        if path not in {"A", "B"}:
            raise KmsError("invalid_path")
        return "shared" if self.settings.mode == "shared" else path

    def refresh(self):
        now = self.clock()
        count = int((now - self.updated) * self.settings.refill_per_s)
        if self.refilling and count:
            for pool in self.available:
                self.available[pool] = min(
                    self.settings.capacity, self.available[pool] + count
                )
            self.updated += count / self.settings.refill_per_s
        elif not self.refilling or not self.settings.refill_per_s:
            self.updated = now
        self.keys = {
            key_id: key for key_id, key in self.keys.items() if key.expires_at > now
        }

    def issue(self, session, path):
        self.refresh()
        pool = self.pool(path)
        if not isinstance(session, str) or not 1 <= len(session) <= 128:
            raise KmsError("invalid_session")
        if not self.available[pool]:
            raise KmsError("empty_pool")
        if len(self.keys) >= self.settings.retained_limit:
            raise KmsError("retained_limit")
        key = Key(
            secrets.token_hex(16),
            session,
            path,
            self.clock() + self.settings.key_ttl_s,
            secrets.token_bytes(32),
        )
        self.available[pool] -= 1
        self.keys[key.key_id] = key
        return key

    def get(self, key_id, session, path):
        self.refresh()
        self.pool(path)
        key = self.keys.get(key_id)
        if key is None:
            raise KmsError("unknown_key_id")
        if (key.session, key.path) != (session, path):
            raise KmsError("key_scope_mismatch")
        return key

    def state(self):
        self.refresh()
        return {
            "available": dict(self.available),
            "retained": len(self.keys),
            "refilling": self.refilling,
        }
