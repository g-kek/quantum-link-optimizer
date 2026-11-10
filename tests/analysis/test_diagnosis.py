import json

import pytest

from quantum_link_optimizer.analysis import examples
from quantum_link_optimizer.analysis.causes import diagnose
from quantum_link_optimizer.analysis.decomposition import decompose
from quantum_link_optimizer.analysis.probes import read_probes, write_probes
from quantum_link_optimizer.analysis.run import diagnose_csv, run_examples
from quantum_link_optimizer.analysis.segments import segments


def probes(name):
    return [probe for probe, _ in examples.build(name)]


def test_every_example_gets_its_known_cause(tmp_path):
    summary = run_examples(tmp_path)

    assert summary["matched"] == summary["total"] == len(examples.EXPECTED)
    assert (tmp_path / "index.html").exists()
    assert (tmp_path / "examples" / "queueing.html").exists()


def test_size_sweep_recovers_the_narrow_link():
    paths = decompose(probes("narrow-bandwidth"))["paths"]
    sweep = paths["A"]["size_sweep"]

    assert sweep["significant"]
    assert sweep["bottleneck_kbit_s"] == pytest.approx(33, rel=0.05)
    assert paths["B"]["size_sweep"] is None


def test_icmp_excess_is_measured_against_udp():
    kinds = decompose(probes("icmp-slow-path"))["paths"]["A"]["probe_types"]

    assert kinds["icmp_excess_ms"] > 600
    assert kinds["floor_ms"]["udp"] < 40


def test_segment_with_the_added_delay_is_named():
    route = segments(probes("constant-delay"))

    assert route["A"]["largest"] == "gw → hub"
    assert "B" not in route


def test_queue_follows_load_and_paths_share_it():
    result = decompose(probes("shared-queue"))

    assert result["paths"]["A"]["load"]["spearman_load_queue"] > 0.5
    assert result["shared"]["spearman_queue"] > 0.6


def test_short_or_empty_series_are_not_diagnosed():
    rows = probes("healthy")[:3]
    report = diagnose(decompose(rows), {}, 100)

    assert report["verdict"]["cause"] == "not_established"


def test_csv_round_trip_and_report(tmp_path):
    source = tmp_path / "ping.csv"
    write_probes(source, examples.build("route-change"))

    assert len(read_probes(source)) == 2 * examples.SAMPLES

    report = diagnose_csv(source, tmp_path / "out", distance_km=1200)
    saved = json.loads((tmp_path / "out" / "diagnosis.json").read_text())

    assert report["diagnosis"]["verdict"]["cause"] == "route_change"
    assert saved["input"]["file"] == "ping.csv"


@pytest.mark.parametrize(
    "change, message",
    [
        (("ok", "12.5"), None),
        (("ok", ""), "only status ok"),
        (("timeout", "5"), "only status ok"),
    ],
)
def test_reader_checks_status_and_rtt(tmp_path, change, message):
    status, rtt = change
    source = tmp_path / "ping.csv"
    source.write_text(
        "sample,timestamp,path,status,rtt_ms,source,provenance,probe\n"
        f"0,2026-11-01T00:00:00+00:00,0,{status},{rtt},real_telemetry,ping,icmp\n",
        encoding="utf-8",
    )

    if message is None:
        assert read_probes(source)[0].path == "A"
    else:
        with pytest.raises(ValueError, match=message):
            read_probes(source)


def test_reader_rejects_unknown_columns(tmp_path):
    source = tmp_path / "ping.csv"
    source.write_text(
        "sample,timestamp,path,status,rtt_ms,source,provenance,colour\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Unknown CSV columns"):
        read_probes(source)
