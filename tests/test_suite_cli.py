"""Exercise the actual CLI dispatcher and saved-model suite, not a mock."""

import json
import subprocess
import sys


def test_benchmark_suite_cli_smoke(tmp_path):
    output = tmp_path / "suite"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "quantum_link_optimizer.cli",
            "benchmark-suite",
            "--output",
            str(output),
            "--seeds",
            "41",
            "--samples",
            "500",
            "--epochs",
            "2",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    report = json.loads(completed.stdout)
    assert report == json.loads((output / "suite-report.json").read_text())
    assert report["seeds"] == [41]
    assert not report["test_tuning"]
    assert [run["scenario"] for run in report["runs"]] == ["normal", "shared-stress"]
    for run in report["runs"]:
        assert run["replay"]["prefix_verified"]
        assert run["replay"]["selected_outcome_coverage"] == 1
        assert run["replay"]["decision_origins"] == 100
