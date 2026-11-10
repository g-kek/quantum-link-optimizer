from quantum_link_optimizer.diagnostics.topology import localize


def test_one_slow_path_points_at_its_segment():
    findings = [{"state": "branch_degradation", "scope": "B"}]
    localize(findings, healthy={"path:A", "keys:A", "keys:B"})

    assert findings[0]["location"]["component"] == "segment:B"


def test_both_paths_slow_stay_ambiguous_without_a_declared_segment():
    findings = [{"state": "branch_degradation", "scope": "A+B"}]
    localize(findings, healthy={"keys:A", "keys:B"})
    location = findings[0]["location"]

    assert location["ambiguous"]
    assert "Общий участок A и B" in location["title"]


def test_declared_shared_segment_is_preferred_when_endpoints_look_fine():
    findings = [{"state": "branch_degradation", "scope": "A+B"}]
    healthy = {"keys:A", "keys:B", "sender_queue", "receiver_spans"}
    localize(findings, healthy=healthy, shared_segment=True)

    assert findings[0]["location"]["component"] == "shared"
