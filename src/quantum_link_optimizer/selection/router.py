import asyncio
import time

from quantum_link_optimizer.commands.message import create

from .budget import CopyBudget, Pacer
from .diagnostics import diagnose
from .policy import Policy
from .race import transmit
from .state import Observations


class Router:
    def __init__(self, senders, settings, emit, allow_copy=False):
        if type(allow_copy) is not bool:
            raise ValueError("copy authorization must be boolean")
        if senders["A"].session.logical_id != senders["B"].session.logical_id:
            raise ValueError("routing requires a shared logical session")
        self.senders = senders
        self.settings = settings
        self.emit = emit
        self.allow_copy = allow_copy
        self.policy = Policy(settings)
        self.observations = Observations(settings)
        self.budget = CopyBudget(
            settings.extra_bytes_per_command, settings.extra_bytes_total
        )
        self.sent_commands = set()
        self.pacer = Pacer(settings.pacing_per_s)
        self.queue = asyncio.Queue(maxsize=settings.queue_limit)
        self.worker = asyncio.create_task(self.process())

    def observe(self, event, **fields):
        self.observations.observe(event, **fields)
        if event == "attempt_sent":
            self.sent_commands.add(fields["command_id"])

    async def send(
        self, value, sequence, deadline_s=1, command_id=None, allow_copy=None
    ):
        if allow_copy is not None and type(allow_copy) is not bool:
            raise ValueError("allow_copy must be boolean")
        copy_allowed = self.allow_copy and allow_copy is not False
        started = time.monotonic_ns()
        body = create(value, sequence, deadline_s, command_id)
        future = asyncio.get_running_loop().create_future()
        self.emit(
            "command_submitted",
            command_id=body["id"],
            deadline_ns=body["deadline_ns"],
            logical_session=self.senders["A"].session.logical_id,
        )
        try:
            self.queue.put_nowait((body, started, future, copy_allowed))
        except asyncio.QueueFull:
            result = {"status": "queue_full", "outcome": "not_sent", "path": None}
            self.complete(body, started, future, result)
        return await future

    def complete(self, body, started, future, result):
        elapsed = time.monotonic_ns() - started
        result = {
            **result,
            "elapsed_ns": elapsed,
            "timely": result["status"] == "executed"
            and elapsed <= body["deadline_ns"] - started,
        }
        self.emit(
            "command_completed",
            command_id=body["id"],
            **{k: v for k, v in result.items() if k != "id"},
        )
        self.sent_commands.discard(body["id"])
        if not future.done():
            future.set_result({"id": body["id"], **result})

    async def process(self):
        while True:
            body, started, future, copy_allowed = await self.queue.get()
            work = None
            cancel_work = None
            try:
                if future.cancelled():
                    continue
                work = asyncio.create_task(self.execute(body, started, copy_allowed))

                def cancel_work(completed, work=work):
                    if completed.cancelled():
                        work.cancel()

                future.add_done_callback(cancel_work)
                result = await work
                self.complete(body, started, future, result)
            except asyncio.CancelledError:
                future.cancel()
                if asyncio.current_task().cancelling():
                    raise
                self.complete(
                    body,
                    started,
                    future,
                    {
                        "status": "cancelled",
                        "path": None,
                        "outcome": "unknown"
                        if body["id"] in self.sent_commands
                        else "not_sent",
                    },
                )
            except Exception as error:
                if not future.done():
                    future.set_exception(error)
                self.emit("controller_error", error=type(error).__name__)
            finally:
                if cancel_work:
                    future.remove_done_callback(cancel_work)
                self.queue.task_done()

    async def execute(self, body, started, copy_allowed):
        if time.monotonic_ns() >= body["deadline_ns"]:
            result = {
                "status": "expired_in_queue",
                "outcome": "not_sent",
                "path": None,
            }
        elif not await self.pacer.wait(body["deadline_ns"]):
            result = {
                "status": "pacing_deadline",
                "outcome": "not_sent",
                "path": None,
            }
        else:
            now = time.monotonic_ns()
            snapshot = self.observations.snapshot(now, self.senders)
            path, reason = self.policy.choose(snapshot, now)
            self.emit(
                "route_decision",
                command_id=body["id"],
                path=path,
                reason=reason,
                observations=snapshot,
                diagnostics=diagnose(
                    snapshot,
                    self.settings.shared_segment,
                    self.queue.qsize(),
                    self.settings.target_ms,
                ),
                local_queue_depth=self.queue.qsize(),
            )
            if path is None:
                result = {
                    "status": "no_eligible_path",
                    "outcome": "not_sent",
                    "path": None,
                }
            else:
                result = await transmit(self, body, started, path, copy_allowed)
        return result

    async def close(self):
        self.worker.cancel()
        await asyncio.gather(self.worker, return_exceptions=True)
        while not self.queue.empty():
            _, _, future, _ = self.queue.get_nowait()
            future.cancel()
            self.queue.task_done()
