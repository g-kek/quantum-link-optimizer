"""Acceptance of observed effects, rather than just validity of netem settings."""

import json
import os
import platform
import re
import shutil
import statistics

import pytest

from quantum_link_optimizer.lab.runner import run_scenario
from quantum_link_optimizer.lab.system import run
from quantum_link_optimizer.telemetry_lab.manifest import verify_files

pytestmark = pytest.mark.skipif(
    platform.system() != "Linux" or os.geteuid() != 0 or not shutil.which("tcpdump"),
    reason="requires root Linux with lab tools",
)


def baseline():
    return {
        "name": "acceptance",
        "topology": "independent",
        "duration_s": 4,
        "interval_s": 0.01,
        "paths": {
            path: {direction: {"delay_ms": 5} for direction in ("forward", "reverse")}
            for path in ("A", "B")
        },
    }


def replies(output, path):
    text = (output / f"{path}.ping.log").read_text()
    return [
        (float(stamp), int(seq), float(rtt))
        for stamp, seq, rtt in re.findall(
            r"\[([\d.]+)\].*icmp_seq=(\d+).*time=([\d.]+) ms", text
        )
    ]


def checked_run(config, output):
    run_scenario(config, output)
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["status"] == "collected"
    assert not manifest["cleanup_errors"]
    assert all(verify_files(output).values())
    topology = json.loads((output / "topology.json").read_text())
    assert all(not os.path.exists(f"/run/netns/{n}") for n in topology["namespaces"])


@pytest.mark.parametrize("repeat", range(3))
def test_forward_loss_is_visible_while_other_path_is_healthy(tmp_path, repeat):
    config = baseline()
    config["paths"]["A"]["forward"]["loss_pct"] = 50
    output = tmp_path / f"loss-{repeat}"
    checked_run(config, output)
    stats = {}
    for path in ("A", "B"):
        text = (output / f"{path}.ping.log").read_text()
        match = re.search(r"(\d+) packets transmitted, (\d+) received", text)
        assert match, text
        sent, received = map(int, match.groups())
        assert sent >= 200
        stats[path] = 1 - received / sent
    assert 0.25 < stats["A"] < 0.75, stats
    assert stats["B"] < 0.05, stats


@pytest.mark.parametrize("repeat", range(3))
def test_outage_has_a_gap_and_recovery_only_on_a(tmp_path, repeat):
    config = baseline()
    config["events"] = [
        {"at_s": 1, "path": "A", "action": "down"},
        {"at_s": 2.5, "path": "A", "action": "up"},
    ]
    output = tmp_path / f"outage-{repeat}"
    checked_run(config, output)
    events = [
        json.loads(line)
        for line in (output / "truth-network.jsonl").read_text().splitlines()
    ]
    assert [event["action"] for event in events] == ["down", "up"]
    for event in events:
        assert event["at_s"] <= event["actual_s"] < event["at_s"] + 0.5
    a, b = replies(output, "A"), replies(output, "B")
    assert len(a) > 100 and len(b) > 250
    gap_index = max(range(len(a) - 1), key=lambda i: a[i + 1][0] - a[i][0])
    assert a[gap_index + 1][0] - a[gap_index][0] > 1
    # ping need not advance sequence while sendmsg fails on a down interface.
    # The observed reply gap and recovery, not an inferred loss count, matter.
    assert len(a[: gap_index + 1]) > 20 and len(a[gap_index + 1 :]) > 20
    assert max(right[0] - left[0] for left, right in zip(b, b[1:], strict=False)) < 0.5


@pytest.mark.parametrize("repeat", range(3))
def test_reordering_is_visible_in_receiver_capture(tmp_path, repeat):
    config = baseline()
    config["interval_s"] = 0.005
    config["paths"]["A"]["forward"] = {"delay_ms": 80, "reorder_pct": 50}
    output = tmp_path / f"reorder-{repeat}"
    checked_run(config, output)
    for path, target in (("A", "10.250.1.1"), ("B", "10.250.1.2")):
        capture = run(
            "tcpdump",
            "-nn",
            "-r",
            output / "r.pcap",
            f"icmp[icmptype] = icmp-echo and dst host {target}",
        )
        sequences = [int(seq) for seq in re.findall(r"seq (\d+)", capture)]
        assert len(sequences) > 200
        inversions = sum(
            right < left for left, right in zip(sequences, sequences[1:], strict=False)
        )
        assert inversions > 10 if path == "A" else inversions == 0


@pytest.mark.parametrize("repeat", range(3))
def test_bad_reverse_path_increases_rtt_without_harming_b(tmp_path, repeat):
    config = baseline()
    config["paths"]["A"]["reverse"]["delay_ms"] = 95
    output = tmp_path / f"reverse-{repeat}"
    checked_run(config, output)
    a = [rtt for _, _, rtt in replies(output, "A")]
    b = [rtt for _, _, rtt in replies(output, "B")]
    assert len(a) > 200 and len(b) > 200
    assert statistics.median(a) - statistics.median(b) > 75
    assert statistics.median(b) < 40


@pytest.mark.parametrize("shared", [False, True])
@pytest.mark.parametrize("repeat", range(3))
def test_background_load_builds_queue_and_delays_expected_paths(
    tmp_path, shared, repeat
):
    config = baseline()
    config["duration_s"] = 6
    config["background"] = [{"path": "A", "rate_mbit": 8}]
    if shared:
        config["topology"] = "shared"
        config["shared"] = {"forward": {"rate_mbit": 1, "delay_ms": 5, "limit": 100}}
    else:
        config["paths"]["A"]["forward"].update(rate_mbit=1, limit=100)
    output = tmp_path / f"congestion-{shared}-{repeat}"
    checked_run(config, output)
    rows = [
        json.loads(line) for line in (output / "queues.jsonl").read_text().splitlines()
    ]
    suffix = "-c" if shared else "-a"
    queues = [
        q for row in rows if row["namespace"].endswith(suffix) for q in row["qdisc"]
    ]
    assert any(q.get("backlog", 0) > 0 for q in queues), queues
    assert any(q.get("drops", 0) > 0 for q in queues), queues
    flow = json.loads((output / "A.iperf.json").read_text())
    assert "error" not in flow, flow
    assert flow["end"]["sum_sent"]["bytes"] > 100_000
    a = [rtt for _, _, rtt in replies(output, "A")]
    b = [rtt for _, _, rtt in replies(output, "B")]
    assert len(a) > 5 and len(b) > 5
    # Under drops, the median of received replies can favor post-load samples.
    # Verify tail delay along with direct backlog/drop and offered-load evidence.
    p95_a = sorted(a)[int(0.95 * (len(a) - 1))]
    p95_b = sorted(b)[int(0.95 * (len(b) - 1))]
    assert p95_a > 40
    if shared:
        assert p95_b > 40
    else:
        assert p95_b < 40
        assert p95_a - p95_b > 25
