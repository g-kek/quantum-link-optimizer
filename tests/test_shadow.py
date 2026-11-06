import csv
import json
from dataclasses import replace

import numpy as np
import pytest
import torch

from quantum_link_optimizer.atlas import atlas_benchmark
from quantum_link_optimizer.observations import COLUMNS, read_observations
from quantum_link_optimizer.risk import load_warning, run_risk
from quantum_link_optimizer.shadow import (
    replay_stream,
    run_shadow,
    synthetic_observations,
)
from quantum_link_optimizer.telemetry import generate_telemetry
from quantum_link_optimizer.warning import WarningForecaster


def checkpoint():
    return {
        "window": 4,
        "horizons": [1, 5],
        "mean": [60, 60],
        "scale": [10, 10],
        "sla_ms": 100,
    }


def test_flush_before_consuming_current_outcome_and_future_isolation(tmp_path):
    observations = synthetic_observations(generate_telemetry(100))
    model = WarningForecaster(4, (1, 5))
    before = {k: v.clone() for k, v in model.state_dict().items()}
    log = tmp_path / "stream.jsonl"
    schedule = [(o.sample, o.timestamp, o.time_seconds) for o in observations]

    def stream():
        for o in observations:
            if o.sample >= 80:
                latest = json.loads(log.read_text().splitlines()[-1])
                assert latest["kind"] == "decision"
                assert latest["sample"] == o.sample
                assert "actual_rtt_ms" not in latest
            yield o

    replay_stream(schedule, stream(), model, checkpoint(), log, 80)
    first = [json.loads(line) for line in log.read_text().splitlines()]
    changed = [
        replace(o, rtt=(500.0, 1000.0)) if o.sample >= 90 else o for o in observations
    ]
    second_log = tmp_path / "changed.jsonl"
    replay_stream(schedule, changed, model, checkpoint(), second_log, 80)
    second = [json.loads(line) for line in second_log.read_text().splitlines()]
    assert [r for r in first if r["kind"] == "decision" and r["sample"] <= 90] == [
        r for r in second if r["kind"] == "decision" and r["sample"] <= 90
    ]
    for k, tensor in model.state_dict().items():
        assert torch.equal(tensor, before[k])


def test_missing_stale_abstain_and_unavailable_counterfactual(tmp_path):
    observations = synthetic_observations(generate_telemetry(100))
    observations[85] = replace(
        observations[85], rtt=(None, 60.0), status=("timeout", "ok")
    )
    observations[95:] = [
        replace(o, time_seconds=o.time_seconds + 10) for o in observations[95:]
    ]
    schedule = [(o.sample, o.timestamp, o.time_seconds) for o in observations]
    log = tmp_path / "stream.jsonl"
    report = replay_stream(
        schedule, observations, WarningForecaster(4, (1, 5)), checkpoint(), log, 0
    )
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    decisions = {r["sample"]: r for r in rows if r["kind"] == "decision"}
    assert decisions[0]["reason"] == "abstain_insufficient_history"
    assert decisions[86]["reason"] == "abstain_missing_history"
    assert decisions[95]["reason"] == "abstain_stale_history"
    assert decisions[90]["decision"] is not None
    assert decisions[99]["decision"] is not None
    evaluation = next(
        r for r in rows if r["sample"] == 85 and r["kind"] == "evaluation"
    )
    assert not evaluation["counterfactual_available"]
    assert evaluation["oracle_min_rtt_ms_evaluation_only"] is None
    assert report["abstentions"] > 0
    assert report["selected_outcome_coverage"] < 1


def test_real_schema_keeps_timestamp_status_and_single_path(tmp_path):
    path = tmp_path / "fixture.csv"
    with path.open("w", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(COLUMNS)
        writer.writerow(
            [0, "2024-01-01T00:00:00Z", 0, "ok", 7, "ripe_atlas", "unit-test-fixture"]
        )
        writer.writerow(
            [
                1,
                "2024-01-01T00:04:00Z",
                0,
                "timeout",
                "",
                "ripe_atlas",
                "unit-test-fixture",
            ]
        )
    rows = read_observations(path)
    assert rows[1].time_seconds - rows[0].time_seconds == 240
    assert rows[0].timestamp == "2024-01-01T00:00:00Z"
    assert rows[0].rtt == (7.0, None)
    assert rows[1].status == ("timeout", "missing")
    assert rows[1].source == "ripe_atlas"
    invalid = path.read_text().replace("timeout,,", "timeout,999,")
    path.write_text(invalid)
    with pytest.raises(ValueError):
        read_observations(path)


def test_saved_artifact_replay_matches_batch_and_rejects_prefix_leak(tmp_path):
    run = tmp_path / "training"
    risk = run_risk(run, samples=500, epochs=2)
    report = run_shadow(
        run / "warning-model.pt", run / "telemetry.csv", tmp_path / "replay"
    )
    assert report["prefix_verified"]
    assert report["abstentions"] == 0
    assert report["decision_origins"] == 100
    np.testing.assert_allclose(
        report["mean_selected_rtt_ms_available_only"],
        risk["policies"]["risk_selector"]["mean_rtt_ms"],
    )
    assert report["switches"] == risk["policies"]["risk_selector"]["switches"]
    with pytest.raises(ValueError, match="validation prefix"):
        run_shadow(
            run / "warning-model.pt",
            run / "telemetry.csv",
            tmp_path / "bad",
            start_sample=300,
        )
    with pytest.raises(ValueError, match="domain-shift"):
        run_shadow(
            run / "warning-model.pt",
            run / "telemetry.csv",
            tmp_path / "bad",
            external=True,
        )
    model, saved = load_warning(run / "warning-model.pt")
    assert saved["risk_configuration"]["min_hold_samples"] == 5
    assert not model.training


def test_atlas_single_path_fixture_no_resampling(tmp_path):
    raw = tmp_path / "atlas-fixture.json"
    rows = [
        {
            "msm_id": 1,
            "prb_id": 2,
            "dst_addr": "fixture-only",
            "timestamp": 1704067200 + t * 240,
            "result": [{"rtt": 7.0}],
        }
        for t in range(20)
    ]
    rows[-1]["result"] = [{"x": "*"}]
    raw.write_text(json.dumps(rows[::-1]))
    report = atlas_benchmark(raw, tmp_path / "atlas")
    assert report["paths"] == 1
    assert report["no_resampling"]
    assert report["sample_interval_seconds_median"] == 240
    assert report["test_missing_or_timeout_origins"] == 1
    observed = read_observations(tmp_path / "atlas" / "atlas-telemetry.csv")
    assert all(o.rtt[1] is None for o in observed)
    assert observed[-1].status == ("timeout", "missing")


def test_invalid_forecast_records_abstention_before_outcome(tmp_path):
    model = WarningForecaster(4, (1, 5))
    with torch.no_grad():
        model.network[-1].bias.fill_(float("nan"))
    observations = synthetic_observations(generate_telemetry(100))
    log = tmp_path / "invalid.jsonl"

    def outcomes():
        for o in observations:
            if o.sample >= 80:
                decision = json.loads(log.read_text().splitlines()[-1])
                assert decision["kind"] == "decision"
                assert decision["reason"] == "abstain_invalid_prediction"
                assert decision["forecast_rtt_ms"] is None
            yield o

    report = replay_stream(
        [(o.sample, o.timestamp, o.time_seconds) for o in observations],
        outcomes(),
        model,
        checkpoint(),
        log,
        80,
    )
    assert report["abstentions"] == 20
    assert report["evaluated_selected_outcomes"] == 0
    assert "NaN" not in log.read_text()


@pytest.mark.parametrize(
    "field,value",
    [
        ("scale", [0, 1]),
        ("mean", [float("nan"), 1]),
        ("horizons", [5, 15]),
        ("schema_version", 999),
    ],
)
def test_corrupt_checkpoint_is_rejected(tmp_path, field, value):
    run_risk(tmp_path, samples=500, epochs=1)
    path = tmp_path / "warning-model.pt"
    saved = torch.load(path, weights_only=True)
    saved[field] = value
    torch.save(saved, path)
    with pytest.raises(ValueError, match="[Cc]heckpoint"):
        load_warning(path)


def test_unreadable_checkpoint_has_a_clear_error(tmp_path):
    path = tmp_path / "broken.pt"
    path.write_bytes(b"not a PyTorch checkpoint")
    with pytest.raises(ValueError, match="Cannot read warning checkpoint"):
        load_warning(path)
