import json

import pytest

from quantum_link_optimizer.atlas import atlas_benchmark


def records():
    return [
        {
            "msm_id": 1,
            "prb_id": 2,
            "dst_addr": "fixture",
            "timestamp": 1704067200 + t * 240,
            "result": [{"rtt": 7.0}],
        }
        for t in range(20)
    ]


def test_breach_after_timeout_is_retained_in_test_summary(tmp_path):
    rows = records()
    rows[16]["result"] = [{"x": "*"}]
    rows[17]["result"] = [{"rtt": 150.0}]
    path = tmp_path / "atlas.json"
    path.write_text(json.dumps(rows))
    report = atlas_benchmark(path, tmp_path / "result")
    assert report["test_available_origins"] == 3
    assert report["evaluated_origins"] == 2
    assert report["baseline_excluded_missing_previous"] == 1
    assert report["test_breaches"] == 1
    assert not report["no_test_events"]
    assert report["test_mean_rtt_ms"] == pytest.approx(164 / 3)
    assert report["baseline_test_breaches"] == 0


@pytest.mark.parametrize(
    "row", [None, {}, {"msm_id": []}, {"msm_id": 1, "prb_id": 2, "timestamp": 1e20}]
)
def test_malformed_atlas_records_have_actionable_errors(tmp_path, row):
    rows = records()
    rows[0] = row
    path = tmp_path / "atlas.json"
    path.write_text(json.dumps(rows))
    with pytest.raises(ValueError, match="Atlas record 0"):
        atlas_benchmark(path, tmp_path / "result")
