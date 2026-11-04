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
