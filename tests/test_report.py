import json
import subprocess
import sys

from quantum_link_optimizer.report import render_report


def test_report_embeds_untrusted_telemetry_as_inert_json(tmp_path):
    payload = {"title": '</script><img src=x onerror="alert(1)">'}
    path = render_report(payload, tmp_path)
    text = path.read_text()
    assert payload["title"] not in text
    assert "__REPORT_DATA__" not in text
    start = text.index('id="report-data"')
    start = text.index(">", start) + 1
    end = text.index("</script>", start)
    assert json.loads(text[start:end]) == payload


def test_demo_cli_exports_replay_and_standalone_report(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "quantum_link_optimizer.cli",
            "demo",
            "--samples",
            "500",
            "--epochs",
            "2",
            "--output",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    assert "index.html" in result.stdout
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["kind"] == "synthetic_demo"
    assert report["scenario"] == "high-rtt"
    assert report["replay_verified"]
    assert report["feasibility"]["both_paths_bad_rate_available_pairs"] == 1
    assert len(report["points"]) == 100
    assert (tmp_path / "index.html").is_file()
    assert (tmp_path / "experiment/replay/shadow.jsonl").is_file()


def test_cli_missing_input_has_no_traceback(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "quantum_link_optimizer.cli",
            "audit",
            "--input",
            str(tmp_path / "missing.csv"),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 2
    assert "No such file" in result.stderr
    assert "Traceback" not in result.stderr
