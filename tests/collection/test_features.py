from quantum_link_optimizer.collection.features import FEATURE_NAMES, rows
from quantum_link_optimizer.collection.preprocess import fit, transform


def event(kind, when, record_id, **fields):
    return {
        "event": kind,
        "available_ns": when,
        "algorithm_available_ns": when,
        "record_id": record_id,
        **fields,
    }


def config():
    return {"episode_id": "test", "split": "train", "target_ms": 100, "max_age_ms": 1}


def test_future_outcome_and_truth_do_not_enter_features():
    events = [
        event("decision", 10, 1, sample=0),
        event("probe_result", 20, 2, sample=0, path="A", status="ok", rtt_ms=700),
        event("probe_result", 30, 3, sample=0, path="B", status="ok", rtt_ms=60),
        event("initial_conditions", 5, 4, profile="hidden", path="A", rtt_ms=1),
        event("decision", 40, 5, sample=1),
    ]
    result = list(rows(config(), events))
    assert result[0]["features"]["last_rtt_ms"] is None
    assert result[0]["targets"]["rtt_ms"] == 700
    assert result[2]["features"]["last_rtt_ms"] == 700
    assert result[3]["features"]["last_rtt_ms"] == 60
    assert all(set(row["features"]) == set(FEATURE_NAMES) for row in result)
    assert all(
        row["feature_available_ns"] is None
        or row["feature_available_ns"] <= row["decision_ns"]
        for row in result
    )


def test_delayed_availability_staleness_and_missing_are_preserved():
    events = [
        event("decision", 100, 1, sample=0),
        event(
            "probe_result", 200, 2, sample=0, path="A", status="timeout", rtt_ms=None
        ),
        event("decision", 2_000_000, 3, sample=1),
    ]
    result = list(rows(config(), events))
    assert result[0]["targets"]["probe_within_target"] is False
    assert result[1]["targets"]["probe_status"] == "missing"
    assert result[1]["targets"]["probe_within_target"] is None
    assert result[2]["features"]["stale"] == 1
    assert result[2]["features"]["last_rtt_ms"] is None


def test_preprocessing_uses_only_train_and_keeps_missing_indicator():
    features = dict.fromkeys(FEATURE_NAMES, None)
    training = {
        "episode_id": "train",
        "split": "train",
        "features": features | {"last_rtt_ms": 60},
    }
    validation = {
        "episode_id": "val",
        "split": "validation",
        "features": features | {"last_rtt_ms": 700},
    }
    stats = fit([training, validation])
    assert stats["features"]["last_rtt_ms"]["mean"] == 60
    assert stats["fit_episode_ids"] == ["train"]
    assert transform(features, stats)["last_rtt_ms_missing"] == 1


def test_causal_policy_cannot_use_oracle_labels():
    from quantum_link_optimizer.collection.audit import Audit

    pair = {
        "A": {
            "features": {"ewma_rtt_ms": 60},
            "targets": {
                "rtt_ms": 700,
                "probe_status": "ok",
                "probe_within_target": False,
            },
        },
        "B": {
            "features": {"ewma_rtt_ms": 700},
            "targets": {
                "rtt_ms": 60,
                "probe_status": "ok",
                "probe_within_target": True,
            },
        },
    }
    audit = Audit()
    audit.add(pair)
    result = audit.result()
    assert result["causal_ewma"]["within_target_fraction"] == 0
    assert result["oracle_bound_future_labels"]["within_target_fraction"] == 1
    for row in pair.values():
        row["targets"].update(rtt_ms=700, probe_within_target=False)
    audit = Audit()
    audit.add(pair)
    assert audit.result()["both_over_target_fraction"] == 1
    assert audit.result()["oracle_bound_future_labels"]["within_target_fraction"] == 0
