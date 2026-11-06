import asyncio
import time


class CopyBudget:
    def __init__(self, per_command, total):
        self.per_command = per_command
        self.total = total
        self.used = 0

    def command(self):
        used = 0

        def reserve(size):
            nonlocal used
            if used + size > self.per_command or self.used + size > self.total:
                return False
            used += size
            self.used += size
            return True

        return reserve


class Pacer:
    def __init__(self, rate_per_s):
        self.rate = rate_per_s
        self.next_ns = 0

    async def wait(self, deadline_ns):
        if not self.rate:
            return True
        now = time.monotonic_ns()
        scheduled = max(now, self.next_ns)
        if scheduled >= deadline_ns:
            return False
        self.next_ns = scheduled + int(1e9 / self.rate)
        await asyncio.sleep(max(0, (scheduled - time.monotonic_ns()) / 1e9))
        return time.monotonic_ns() < deadline_ns
