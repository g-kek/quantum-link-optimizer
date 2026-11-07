import json
from datetime import UTC, datetime
from pathlib import Path

from quantum_link_optimizer.experiments.common import write_json
from quantum_link_optimizer.telemetry_lab.manifest import digest, verify_files


def create_lock(development, plan, destination):
    source = json.loads((development / "scenario.json").read_text())
    used = set(source["train_seeds"]) | set(source["validation_seeds"])
    requested = set(plan["final_seeds"])

    if not requested or requested & used:
        raise ValueError("independent seeds overlap development")

    if requested != set(source["final_seeds"]):
        raise ValueError("final seeds differ from the predeclared reservation")

    checks = verify_files(development)
    manifest = json.loads((development / "manifest.json").read_text())

    if manifest["status"] != "collected" or not checks or not all(checks.values()):
        raise ValueError("development evidence is incomplete or corrupted")

    if destination.exists():
        raise FileExistsError("refusing to replace an existing policy lock")

    lock = {
        "schema_version": 1,
        "frozen_utc": datetime.now(UTC).isoformat(),
        "plan": plan,
        "development_seeds": sorted(used),
        "files": {
            name: {
                "path": str((development / name).resolve()),
                "sha256": digest(development / name),
            }
            for name in ("model.json", "manifest.json", "forecasting-report.json")
        },
    }
    write_json(destination, lock)

    return lock


def read_lock(path):
    lock = json.loads(path.read_text())

    if lock["schema_version"] != 1:
        raise ValueError("unsupported validation lock")

    if set(lock["plan"]["final_seeds"]) & set(lock["development_seeds"]):
        raise ValueError("invalid independent seed separation")

    for record in lock["files"].values():
        if digest(Path(record["path"])) != record["sha256"]:
            raise ValueError("frozen development/model changed")

    return lock
