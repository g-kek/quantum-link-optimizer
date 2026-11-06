import json
import os
import platform
import re
import shutil

import pytest

from quantum_link_optimizer.lab.runner import run_scenario
from quantum_link_optimizer.lab.system import run

pytestmark = pytest.mark.skipif(
    platform.system() != "Linux" or os.geteuid() != 0 or not shutil.which("tcpdump"),
    reason="requires root Linux with lab tools",
)


@pytest.mark.parametrize("shared", [False, True])
def test_routes_delay_and_cleanup(tmp_path, shared):
    config = {
        "name": "smoke",
        "topology": "shared" if shared else "independent",
        "duration_s": 2,
        "interval_s": 0.1,
        "paths": {
            branch: {
                direction: {"delay_ms": 20} for direction in ("forward", "reverse")
            }
            for branch in ("A", "B")
        },
    }
    output = tmp_path / "run"
    run_scenario(config, output)
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["status"] == "collected"
    assert not manifest["cleanup_errors"]
    for branch in ("A", "B"):
        log = (output / f"{branch}.ping.log").read_text()
        times = [float(value) for value in re.findall(r"time=([\d.]+) ms", log)]
        assert len(times) >= 5, log
        assert min(times) >= 38, log
        capture = output / f"{branch.lower()}.pcap"
        assert capture.stat().st_size > 24
        other_target = "10.250.1.2" if branch == "A" else "10.250.1.1"
        assert not run(
            "tcpdump", "-nn", "-r", capture, "icmp", "and", "host", other_target
        ).strip()
    topology = json.loads((output / "topology.json").read_text())
    for name in topology["namespaces"]:
        assert not os.path.exists(f"/run/netns/{name}")


def test_cleanup_after_setup_failure(tmp_path, monkeypatch):
    from quantum_link_optimizer.lab import runner
    from quantum_link_optimizer.lab.system import run

    before = run("ip", "netns", "list")

    def fail(*args):
        raise RuntimeError("injected setup failure")

    monkeypatch.setattr(runner, "configure", fail)
    output = tmp_path / "failed"
    with pytest.raises(RuntimeError, match="injected"):
        run_scenario({"topology": "independent"}, output)
    assert run("ip", "netns", "list") == before
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["status"] == "failed"
    assert not manifest["cleanup_errors"]


def test_command_agents_and_hashes(tmp_path):
    from quantum_link_optimizer.telemetry_lab.manifest import verify_files

    config = {
        "name": "commands",
        "topology": "independent",
        "duration_s": 2,
        "interval_s": 0.1,
        "paths": {
            branch: {
                direction: {"delay_ms": 10} for direction in ("forward", "reverse")
            }
            for branch in ("A", "B")
        },
        "commands": {
            "path": "A",
            "count": 3,
            "deadline_s": 0.5,
            "kms_config": "lab/kms/normal.json",
        },
    }
    output = tmp_path / "commands"
    run_scenario(config, output)
    summary = json.loads((output / "summary.json").read_text())
    assert summary["timely_executed"] == 3
    assert all(verify_files(output).values())
    assert not run(
        "tcpdump", "-nn", "-r", output / "s.pcap", "tcp", "port", "9400"
    ).strip()


def test_shared_fq_codel_is_a_real_rate_limited_qdisc(tmp_path):
    from pathlib import Path

    from quantum_link_optimizer.lab.config import load_config

    config = load_config(Path("lab/scenarios/shared-fq-codel.json"))
    config["duration_s"] = 2
    output = tmp_path / "fq-codel"
    run_scenario(config, output)
    final = json.loads((output / "final.json").read_text())
    common = next(value for name, value in final.items() if name.endswith("-c"))
    kinds = {qdisc["kind"] for qdisc in common["queues"]}
    assert {"tbf", "fq_codel"} <= kinds
    assert not json.loads((output / "manifest.json").read_text())["cleanup_errors"]


def test_online_controller_selects_actual_b_namespace_route(tmp_path):
    from pathlib import Path

    from quantum_link_optimizer.lab.config import load_config

    config = load_config(Path("lab/scenarios/online-selection.json"))
    config["duration_s"] = 3
    config["commands"].update(count=3, interval_s=0.1, warmup_s=0.6)
    # Isolate route selection from startup hysteresis (covered in policy tests).
    config["commands"]["routing"]["min_hold_ms"] = 0
    output = tmp_path / "online"
    run_scenario(config, output)
    summary = json.loads((output / "routing-summary.json").read_text())
    assert summary["executions"] == summary["offered"] == 3
    assert summary["duplicate_executions"] == 0
    assert run("tcpdump", "-nn", "-r", output / "b.pcap", "udp", "port", "9502").strip()
    assert not run(
        "tcpdump", "-nn", "-r", output / "a.pcap", "udp", "port", "9501"
    ).strip()
