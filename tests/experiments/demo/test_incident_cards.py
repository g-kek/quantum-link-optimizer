from quantum_link_optimizer.experiments.demo.build import incident, stages


def test_old_journal_incident_gets_class_and_title():
    event = {
        "event": "incident_opened",
        "incident_id": "incident-1",
        "state": "kms_delay",
        "scope": "A",
        "measurements": [{"metric": "kms_request_ms", "value": 60, "unit": "ms"}],
        "alternatives": ["empty_pool"],
    }
    card = incident(event, 1.5)

    assert card["class"] == "key_delivery_delay"
    assert card["title"] == "Медленная выдача ключей"
    assert card["lifecycle"] == "open"
    assert card["location"] is None


def test_location_keeps_component_and_candidates():
    event = {
        "event": "incident_resolved",
        "incident_id": "incident-2",
        "state": "branch_degradation",
        "scope": "A+B",
        "location": {
            "title": "Общий участок A и B",
            "component": "shared",
            "ambiguous": False,
            "candidates": [{"component": "shared"}, {"component": "sender"}],
        },
    }
    card = incident(event, 2.0)

    assert card["lifecycle"] == "resolved"
    assert card["location"]["candidates"] == ["shared", "sender"]


def test_stages_summarize_the_tail():
    sender = []

    for index in range(10):
        command = f"command-{index}"
        sender += [
            {"event": "route_decision", "command_id": command, "queue_wait_ns": 0},
            {
                "event": "command_completed",
                "command_id": command,
                "status": "executed",
                "elapsed_ns": int((50 + 10 * index) * 1e6),
            },
        ]

    result = stages(sender)

    assert len(result["commands"]) == 10
    assert result["tail"]["commands"] == 2
    assert result["labels"]["network"] == "Сеть и повторы"
