import csv
from pathlib import Path

import pytest

from quantum_link_optimizer.audit import run_audit
from quantum_link_optimizer.observations import COLUMNS


def write_fixture(path: Path, rows):
    with path.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(COLUMNS)
        for sample, pair in enumerate(rows):
            for channel, value in enumerate(pair):
                writer.writerow(
                    [
                        sample,
                        f"2026-01-01T00:00:{sample:02d}Z",
                        channel,
                        "timeout" if value is None else "ok",
                        "" if value is None else value,
                        "real_telemetry",
                        "unit-test fixture, not operator data",
                    ]
                )


def test_high_rtt_cannot_be_fixed_by_selecting_paths(tmp_path):
    path = tmp_path / "ping.csv"
    write_fixture(path, [(700, 710)] * 6)
    report = run_audit(path, tmp_path / "report")
    assert report["feasibility"]["both_paths_bad_rate_available_pairs"] == 1
    assert report["feasibility"]["oracle_min_p95_ms_available_pairs"] == 700
    assert report["kind"] == "ping_audit"
    assert all(p["sla_violation_rate"] == 1 for p in report["policies"].values())


def test_timeouts_and_abstentions_are_not_successes(tmp_path):
    path = tmp_path / "ping.csv"
    write_fixture(path, [(50, 60), (None, 70), (80, 90), (90, 80)])
    report = run_audit(path, tmp_path / "report")
    policy = report["policies"]["previous_rtt_constrained"]
    assert policy["selected_timeouts"] == 1
    assert policy["abstentions"] == 1
    assert policy["selected_outcome_coverage"] == pytest.approx(1 / 3)
    assert report["feasibility"]["paired_coverage"] == pytest.approx(2 / 3)


def test_baselines_cannot_see_current_or_future_rtt(tmp_path):
    path = tmp_path / "ping.csv"
    rows = [(50, 60)] * 8
    write_fixture(path, rows)
    before = run_audit(path, tmp_path / "before")
    rows[4:] = [(900, 10)] * 4
    write_fixture(path, rows)
    after = run_audit(path, tmp_path / "after")
    for a, b in zip(before["points"], after["points"], strict=True):
        if a["sample"] <= 4:
            assert a["choices"] == b["choices"]
            assert a["reasons"] == b["reasons"]
