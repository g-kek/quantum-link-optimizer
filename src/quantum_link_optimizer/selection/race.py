import asyncio
import time

import numpy as np


def hedge_plan(router, primary, started):
    """Delay before a copy and the share of recent probes that were too slow."""
    settings = router.settings
    now = time.monotonic_ns()
    recent = router.observations.recent_rtt(primary, now)

    if not settings.adaptive_hedge or len(recent) < 5:
        return settings.hedge_delay_ms or 0, None

    budget = settings.target_ms - (now - started) / 1e6
    delay = float(np.quantile(recent, 0.9))
    risk = float(np.mean(np.asarray(recent) > budget))
    return min(delay, max(budget, 0)), risk


async def transmit(router, body, started, primary, copy_allowed):
    settings = router.settings
    futures = {}
    results = []
    delay_ms, risk = hedge_plan(router, primary, started)
    hedge_at = time.monotonic_ns() + int(delay_ms * 1e6)
    copy_pending = settings.hedge_delay_ms is not None

    if copy_pending and risk is not None and risk < settings.hedge_risk:
        router.emit(
            "copy_suppressed", command_id=body["id"], reason="risk_low", risk=risk
        )
        copy_pending = False

    def launch(path, extra=False):
        reserve = router.budget.command() if extra else None
        task = asyncio.create_task(
            router.senders[path].submit(
                body,
                started_ns=started,
                reserve=reserve,
                max_attempts=settings.max_attempts_per_leg,
            )
        )
        futures[task] = path
        router.emit("leg_started", command_id=body["id"], path=path, extra=extra)

    launch(primary)
    try:
        while time.monotonic_ns() < body["deadline_ns"]:
            now = time.monotonic_ns()
            if copy_pending and now >= hedge_at:
                snapshot = router.observations.snapshot(now, router.senders)
                alternative, reason = router.policy.alternative(primary, snapshot)
                if (
                    router.budget.used >= router.budget.total
                    or settings.extra_bytes_per_command == 0
                ):
                    alternative, reason = None, "byte_budget"
                if alternative and copy_allowed:
                    launch(alternative, extra=True)
                else:
                    router.emit(
                        "copy_suppressed",
                        command_id=body["id"],
                        reason=reason if not alternative else "not_authorized",
                    )
                copy_pending = False
            if not futures:
                if not copy_pending:
                    break
                await asyncio.sleep(
                    max(0, min(hedge_at, body["deadline_ns"]) - now) / 1e9
                )
                continue
            until = (
                min(hedge_at, body["deadline_ns"])
                if copy_pending
                else body["deadline_ns"]
            )
            done, _ = await asyncio.wait(
                futures,
                timeout=max(0, (until - now) / 1e9),
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                path = futures.pop(task)
                result = task.result()
                results.append((path, result))
                if result["status"] == "executed":
                    return {**result, "path": path}
        if body["id"] in router.sent_commands:
            return {"status": "no_ack", "outcome": "unknown", "path": primary}
        if results:
            path, result = results[0]
            return {**result, "path": path, "outcome": "not_sent"}
        return {"status": "expired_before_send", "outcome": "not_sent", "path": primary}
    finally:
        for task in futures:
            task.cancel()
        await asyncio.gather(*futures, return_exceptions=True)
