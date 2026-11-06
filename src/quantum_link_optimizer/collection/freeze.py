import json

from quantum_link_optimizer.telemetry_lab.manifest import digest, verify_files

from .features import FEATURE_SCHEMA_VERSION


def freeze(dataset, policy, output):
    manifest = json.loads((dataset / "manifest.json").read_text())
    checks = verify_files(dataset)
    if not manifest.get("complete") or not checks or not all(checks.values()):
        raise ValueError("freeze requires a complete, intact development dataset")
    if any(source["split"] == "final_test" for source in manifest["sources"]):
        raise ValueError("cannot freeze using final-test data")
    stats = json.loads((dataset / "preprocessing.json").read_text())
    if stats.get("feature_schema_version") != FEATURE_SCHEMA_VERSION:
        raise ValueError("rebuild dataset for current feature schema before freeze")
    files = {
        "policy": policy,
        "preprocessing": dataset / "preprocessing.json",
        "dataset_manifest": dataset / "manifest.json",
    }
    lock = {
        name: {"path": str(path.resolve()), "sha256": digest(path)}
        for name, path in files.items()
    }
    with output.open("x") as handle:
        json.dump(lock, handle, indent=2)
        handle.write("\n")
    return lock


def read_lock(path):
    from pathlib import Path

    lock = json.loads(path.read_text())
    if set(lock) != {"policy", "preprocessing", "dataset_manifest"}:
        raise ValueError("invalid frozen policy lock")
    for record in lock.values():
        if digest(Path(record["path"])) != record["sha256"]:
            raise ValueError("frozen policy, preprocessing or dataset changed")
    stats = json.loads(Path(lock["preprocessing"]["path"]).read_text())
    if stats.get("feature_schema_version") != FEATURE_SCHEMA_VERSION:
        raise ValueError("frozen feature schema changed; rebuild and freeze again")
    return lock
