import hashlib
import json
import platform
import sqlite3
import subprocess
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def git(*args):
    try:
        return subprocess.run(
            ["git", *args], check=True, capture_output=True, text=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def snapshot_source(output):
    root = Path(__file__).resolve().parents[3]
    paths = list((root / "src").rglob("*.py")) + list((root / "lab").rglob("*.json"))
    paths += [root / "pyproject.toml", root / "uv.lock"]
    with zipfile.ZipFile(output / "source.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(paths):
            if path.is_file():
                archive.write(path, path.relative_to(root))


def begin(output, config):
    output.mkdir(parents=True, exist_ok=False)
    (output / "scenario.json").write_text(json.dumps(config, indent=2) + "\n")
    snapshot_source(output)
    import importlib.metadata

    versions = {}
    for name in ("cryptography", "quantum-link-optimizer"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return {
        "schema_version": 1,
        "started_utc": datetime.now(UTC).isoformat(),
        "clock_anchor": {"monotonic_ns": time.monotonic_ns(), "utc_ns": time.time_ns()},
        "commit": git("rev-parse", "HEAD"),
        "dirty": git("status", "--porcelain"),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "versions": versions,
        "seed": config.get("seed"),
        "status": "running",
        "split": config.get("split", "development"),
        "clock_scope": "single_host_monotonic",
    }


def finish(output, manifest):
    database = output / "receiver.sqlite"
    if database.with_name(database.name + "-wal").exists():
        connection = None
        try:
            connection = sqlite3.connect(database)
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error as error:
            manifest["database_error"] = type(error).__name__
        finally:
            if connection:
                connection.close()
    manifest["finished_utc"] = datetime.now(UTC).isoformat()
    manifest["files"] = {
        str(path.relative_to(output)): {
            "sha256": digest(path),
            "bytes": path.stat().st_size,
        }
        for path in sorted(output.rglob("*"))
        if path.is_file() and path.name != "manifest.json"
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


def verify_files(output):
    manifest = json.loads((output / "manifest.json").read_text())
    results = {}
    root = output.resolve()
    for name, record in manifest["files"].items():
        path = (root / name).resolve()
        results[name] = (
            path.is_relative_to(root)
            and path.is_file()
            and digest(path) == record["sha256"]
        )
    return results
