def diagnose(snapshot, shared_segment=False, local_queue_depth=0, target_ms=100):
    findings = []
    if local_queue_depth:
        findings.append(
            {
                "scope": "sender",
                "status": "observed_signals",
                "evidence": ["logical_queue"],
                "queue_depth": local_queue_depth,
                "cause": "local_wait_observed",
                "age_ms": 0,
                "alternatives": ["slow_network", "slow_kms", "excess_offered_load"],
            }
        )
    slow = [
        path
        for path, row in snapshot.items()
        if row["ewma_rtt_ms"] is not None and row["ewma_rtt_ms"] > target_ms
    ]
    for path, row in snapshot.items():
        evidence = []
        if row["sender_queue_depth"]:
            evidence.append("sender_queue")
        if row["device_available"] is False:
            evidence.append("reported_device_down")
        if row["device_queued_bytes"]:
            evidence.append("reported_queue")
        if row["last_key_wait_ms"] is not None and row["last_key_wait_ms"] > 10:
            evidence.append("observed_key_wait")
        for name in (
            "receiver_queue_wait_ns",
            "receiver_key_wait_ns",
            "receiver_processing_ns",
        ):
            if row.get(name) is not None and row[name] > 1_000_000:
                evidence.append(name)
        if row["bc_reported_usable"] is False:
            evidence.append("reported_clock_unusable")
        if evidence:
            findings.append(
                {
                    "scope": path,
                    "evidence": evidence,
                    "age_ms": row["observation_age_ms"],
                    "status": "observed_signals",
                    "cause": "not_established",
                    "evidence_age_ms": {
                        "path": row["observation_age_ms"],
                        "key": row["key_observation_age_ms"],
                        "receiver": row.get("receiver_observation_age_ms"),
                    },
                    "alternatives": ["local_load", "path_congestion", "kms_wait"],
                }
            )
    if len(slow) == 2:
        findings.append(
            {
                "scope": "A+B",
                "evidence": ["both_rtt_high"],
                "status": "candidate",
                "cause": "shared_or_parallel_slowdown",
                "alternatives": ["shared_segment", "independent_path_problems"],
                "known_shared_segment": shared_segment,
                "age_ms": {
                    path: row["observation_age_ms"] for path, row in snapshot.items()
                },
            }
        )
    if not findings:
        findings.append(
            {
                "scope": "system",
                "status": "insufficient_evidence",
                "cause": "not_established",
                "alternatives": ["receiver", "unobserved_network_segment"],
                "age_ms": {
                    path: row["observation_age_ms"] for path, row in snapshot.items()
                },
            }
        )
    return findings
