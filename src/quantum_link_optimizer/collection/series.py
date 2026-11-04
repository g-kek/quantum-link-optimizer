import json
import shutil
from pathlib import Path

from quantum_link_optimizer.telemetry_lab.manifest import digest, verify_files

from .collect import collect_episode
from .freeze import read_lock
from .plan import episodes


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


async def collect_series(plan, output, splits, resume=False, frozen_policy=None):
    configs = [config for config in episodes(plan) if config["split"] in splits]
    if not configs or set(splits) - set(plan["splits"]):
        raise ValueError("invalid or empty split selection")
    if "final_test" in splits and set(splits) != {"final_test"}:
        raise ValueError("collect final_test separately")
    if "final_test" in splits and frozen_policy is None:
        raise ValueError("final_test requires a frozen policy file")
    lock = read_lock(frozen_policy) if frozen_policy else None
    policy_hash = digest(frozen_policy) if frozen_policy else None
    identity = {
        "schema_version": 1,
        "plan": plan,
        "splits": sorted(splits),
        "frozen_policy_sha256": policy_hash,
        "frozen": lock,
        "episodes": configs,
        "backend": "loopback",
    }
    inventory = output / "series.json"
    if resume:
        if json.loads(inventory.read_text())["definition"] != identity:
            raise ValueError("resume requires identical plan, splits and frozen policy")
    else:
        output.mkdir(parents=True, exist_ok=False)
        write_json(inventory, {"definition": identity, "status": "running"})
    try:
        for config in configs:
            path = output / config["episode_id"]
            if path.exists():
                manifest = json.loads((path / "manifest.json").read_text())
                checks = verify_files(path)
                if (
                    manifest["status"] != "collected"
                    or not checks
                    or not all(checks.values())
                    or json.loads((path / "scenario.json").read_text()) != config
                ):
                    raise ValueError(f"cannot resume invalid episode {path.name}")
                continue
            if shutil.disk_usage(output).free < 256 * 1024**2:
                raise OSError(
                    "less than 256 MiB free; collection stopped before next episode"
                )
            await collect_episode(config, path)
        write_json(inventory, {"definition": identity, "status": "collected"})
    except BaseException:
        write_json(inventory, {"definition": identity, "status": "incomplete"})
        raise


def load_series(path):
    inventory = json.loads((Path(path) / "series.json").read_text())
    return inventory["definition"]
