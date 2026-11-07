import json

import pytest

from quantum_link_optimizer.experiments.demo.build import (
    render_html,
    trial_path,
    verified,
)
from quantum_link_optimizer.telemetry_lab.manifest import digest


def test_embedded_json_cannot_close_its_script_element():
    data = {"label": '</script><script>alert("injected")</script>__STYLE__'}
    html = render_html(data)
    start = html.index('<script id="report-data" type="application/json">')
    serialized = html[start:].split(">", 1)[1].split("</script>", 1)[0]

    assert "<" not in serialized
    assert json.loads(serialized) == data
    assert "<style>__STYLE__" not in html
    assert "<script>__SCRIPT__" not in html
    assert "<script src=" not in html
    assert "<link " not in html


def test_demo_requires_complete_evidence_and_checks_payload_hash(tmp_path):
    payload = tmp_path / "result.json"
    payload.write_text('{"offered":22}')
    manifest = {
        "status": "collected",
        "files": {"result.json": {"sha256": digest(payload)}},
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))

    assert verified(tmp_path) == manifest

    payload.write_text('{"offered":23}')

    with pytest.raises(ValueError, match="corrupted"):
        verified(tmp_path)

    manifest.update(status="failed", files={})
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))

    with pytest.raises(ValueError, match="incomplete"):
        verified(tmp_path)


def test_trial_paths_reject_traversal_absolute_paths_and_symlinks(tmp_path):
    root = tmp_path / "final"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)

    for path in ("../outside", str(outside), "escape"):
        with pytest.raises(ValueError, match="escapes"):
            trial_path(root, {"relative_path": path})

    assert trial_path(root, {"relative_path": "trials/one"}) == root / "trials/one"
