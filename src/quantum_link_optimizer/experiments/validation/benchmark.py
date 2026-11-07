from quantum_link_optimizer.commands.wire import MAX_DATAGRAM, seal
from quantum_link_optimizer.experiments.forecasting.policy import ForecastPolicy
from quantum_link_optimizer.experiments.runtime import run_trial
from quantum_link_optimizer.experiments.scenarios import configuration
from quantum_link_optimizer.kms.model import Pools
from quantum_link_optimizer.kms.model import Settings as KmsSettings
from quantum_link_optimizer.selection.config import Settings

from .metrics import trial_metrics


def payload_contract():
    key = Pools(KmsSettings()).issue("benchmark", "A")

    try:
        seal(key, "command", {"payload": "x" * 1024})
    except ValueError as error:
        wire_result = str(error)
    else:
        wire_result = "fits wire encoding, but not the scalar command schema"

    return {
        "requested_application_bytes": 1024,
        "wire_limit_bytes": MAX_DATAGRAM,
        "status": "unsupported",
        "evidence": wire_result,
        "reason": (
            "The command contract is scalar set; 1024 application bytes are "
            "not interchangeable with 1024 UDP bytes."
        ),
    }


async def benchmark(output, requested, model):
    rows = []

    for policy in ("bypass", "fixed-b", "ewma", "forecast"):
        for logging in ("sync", "deferred"):
            name = f"{policy}-{logging}"
            config = configuration(
                "normal",
                900001,
                count=requested["messages"],
                interval_s=1 / requested["rate_per_s"],
            )
            config.update(
                bypass=policy == "bypass",
                logging_mode=logging,
                deadline_s=0.2,
                warmup_s=0.1,
                rotate_after=10000,
            )
            config["kms"].update(capacity=10000, initial=10000, refill_per_s=0)

            for settings in config["network"].values():
                for direction in settings.values():
                    direction["delay_ms"] = 0

            settings = Settings(
                mode="ewma" if policy in {"ewma", "forecast"} else "fixed-b",
                queue_limit=64,
                min_hold_ms=0,
            )
            path = output / name
            factory = (
                (lambda current: ForecastPolicy(current, model))
                if policy == "forecast"
                else None
            )
            summary = await run_trial(config, path, settings, factory)
            rows.append(
                {
                    "policy": policy,
                    "logging_mode": logging,
                    **trial_metrics(path, summary, "normal"),
                }
            )
            print(
                f"benchmark {name}: "
                f"{rows[-1]['timely_executed']}/{rows[-1]['offered']}",
                flush=True,
            )

    return {
        "requested": requested,
        "payload_contract": payload_contract(),
        "trials": rows,
        "decision_budget_us": 100,
        "agent_budget_ms": 1,
        "requested_full_workload_budget_status": "not_validated",
        "logging_note": (
            "Deferred journals retain JSON encoding and memory writes; only "
            "disk I/O is deferred. This is not logging disabled."
        ),
        "scope": (
            "1000/s offered open-loop on co-located scalar protected commands; "
            "not the unsupported 1024-byte application workload"
        ),
    }
