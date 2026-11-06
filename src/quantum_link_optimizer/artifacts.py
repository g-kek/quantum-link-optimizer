"""Strict JSON artifacts and the environment that produced them."""

import json
import platform
import subprocess
from importlib.metadata import version
from pathlib import Path


def dump_json(path, report):
    path.write_text(
        json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )


def runtime_metadata():
    revision, dirty = None, None
    try:
        root = Path(__file__).resolve().parents[2]
        if (root / ".git").exists():
            revision = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=root, text=True, timeout=5
            ).strip()
            dirty = bool(
                subprocess.check_output(
                    ["git", "status", "--porcelain"], cwd=root, text=True, timeout=5
                ).strip()
            )
    except (OSError, subprocess.SubprocessError):
        pass
    return {
        "artifact_schema_version": 1,
        "git_commit": revision,
        "working_tree_dirty": dirty,
        "python": platform.python_version(),
        "package": version("quantum-link-optimizer"),
        "numpy": version("numpy"),
        "torch": version("torch"),
    }
