"""Symptoms and bounded hypotheses from delivered observations only."""

import time


def assess(
    snapshot,
    shared_segment=False,
    local_queue_depth=0,
    target_ms=100,
    max_age_ms=2000,
    *,
    now_ns=None,
    failure_threshold=2,
    admission_rejections=0,
):
    now = time.monotonic_ns() if now_ns is None else now_ns
    findings = []

    def fresh(age):
        return age is not None and 0 <= age <= max_age_ms

    def measurement(name, value, unit, age, source):
        return {
            "metric": name,
            "value": value,
            "unit": unit,
            "age_ms": age,
            "observed_ns": now - int(age * 1e6),
            "source": source,
        }

    def add(state, scope, measurements, evidence, alternatives, **extra):
        findings.append(
            {
                "diagnostic_schema_version": 1,
                "state": state,
                "scope": scope,
                "status": "observed_signals",
                "cause": "not_established",
                "evidence": evidence,
                "measurements": measurements,
                "age_ms": max((m["age_ms"] for m in measurements), default=None),
                "evidence_age_ms": {m["metric"]: m["age_ms"] for m in measurements},
                "first_observed_ns": now,
                "last_observed_ns": now,
                "detected_ns": now,
                "physical_onset_ns": None,
                "alternatives": alternatives,
                **extra,
            }
        )

    if local_queue_depth or admission_rejections:
        add(
            "local_overload",
            "sender",
            [
                measurement("queue_depth", local_queue_depth, "commands", 0, "sender"),
                measurement(
                    "recent_admission_rejections",
                    admission_rejections,
                    "commands",
                    0,
                    "sender",
                ),
            ],
            ["logical_queue"] if local_queue_depth else ["admission_rejected"],
            ["slow_network", "slow_kms", "excess_offered_load"],
            cause="local_wait_observed",
            age_ms=0,
            queue_depth=local_queue_depth,
        )

    slow = []
    branch_measurements = {}
    branch_evidence = {}
    empty_pools = set()

    for path, row in snapshot.items():
        age = row.get("observation_age_ms")
        measures, evidence = [], []

        if fresh(age):
            rtt = row.get("ewma_rtt_ms")
            timeouts = row.get("consecutive_timeouts", 0)

            if rtt is not None and rtt > target_ms:
                measures.append(measurement("ewma_rtt_ms", rtt, "ms", age, path))
                evidence.append("rtt_high")

            if timeouts >= failure_threshold:
                measures.append(
                    measurement("consecutive_timeouts", timeouts, "probes", age, path)
                )
                evidence.append("repeated_timeouts")

        device_age = row.get("device_observation_age_ms")

        if fresh(device_age):
            for name, unit, signal in (
                ("device_available", "boolean", "reported_device_down"),
                ("device_queued_bytes", "bytes", "reported_queue"),
            ):
                value = row.get(name)
                abnormal = value is False if name == "device_available" else value

                if abnormal:
                    measures.append(
                        measurement(name, value, unit, device_age, f"device/{path}")
                    )
                    evidence.append(signal)

        if evidence:
            slow.append(path)
            branch_measurements[path] = measures
            branch_evidence[path] = evidence

        queue_age = row.get("sender_queue_observation_age_ms")

        if row.get("sender_queue_depth") and fresh(queue_age):
            add(
                "local_overload",
                path,
                [
                    measurement(
                        "sender_queue_depth",
                        row["sender_queue_depth"],
                        "commands",
                        queue_age,
                        f"sender_queue/{path}",
                    )
                ],
                ["sender_queue"],
                ["slow_network", "slow_kms", "excess_offered_load"],
            )

        kms_age = row.get("kms_observation_age_ms")
        pool = row.get("kms_pool", path)

        if (
            fresh(kms_age)
            and row.get("kms_available_keys") == 0
            and pool not in empty_pools
        ):
            empty_pools.add(pool)
            add(
                "key_pool_empty",
                "KMS" if pool == "shared" else path,
                [measurement("kms_available_keys", 0, "keys", kms_age, f"kms/{path}")],
                ["observed_empty_pool"],
                ["demand_exceeds_refill", "refill_stopped", "initial_pool_empty"],
                pool=pool,
            )

        waits = []
        key_age = row.get("key_observation_age_ms")

        if fresh(key_age) and (row.get("last_key_wait_ms") or 0) > 10:
            waits.append(
                measurement(
                    "last_key_wait_ms",
                    row["last_key_wait_ms"],
                    "ms",
                    key_age,
                    f"key/{path}",
                )
            )

        if (row.get("active_key_wait_ms") or 0) > 10:
            waits.append(
                measurement(
                    "active_key_wait_ms",
                    row["active_key_wait_ms"],
                    "ms",
                    0,
                    f"active_wait/{path}",
                )
            )

        if fresh(kms_age) and (row.get("kms_request_ms") or 0) > 10:
            waits.append(
                measurement(
                    "kms_request_ms",
                    row["kms_request_ms"],
                    "ms",
                    kms_age,
                    f"kms/{path}",
                )
            )

        if waits:
            add(
                "kms_delay",
                path,
                waits,
                ["observed_key_wait"],
                ["empty_pool", "kms_service_queue", "kms_api_transport"],
                evidence_age_ms={"key": key_age, "inventory": kms_age},
            )

        receiver_age = row.get("receiver_observation_age_ms")
        receiver = []

        if fresh(receiver_age):
            for name in (
                "receiver_queue_wait_ns",
                "receiver_key_wait_ns",
                "receiver_processing_ns",
            ):
                if (row.get(name) or 0) > 1_000_000:
                    receiver.append(
                        measurement(
                            name, row[name], "ns", receiver_age, f"receiver/{path}"
                        )
                    )

        if receiver:
            add(
                "receiver_problem",
                path,
                receiver,
                [m["metric"] for m in receiver],
                ["receiver_load", "receiver_queue", "receiver_kms_wait"],
                evidence_age_ms={"receiver": receiver_age},
            )

        clock_age = row.get("bc_observation_age_ms")

        if fresh(clock_age) and row.get("bc_reported_usable") is False:
            add(
                "clock_unusable",
                path,
                [
                    measurement(
                        "bc_reported_usable",
                        False,
                        "boolean",
                        clock_age,
                        f"clock/{path}",
                    )
                ],
                ["reported_clock_unusable"],
                ["holdover", "clock_monitor_bias"],
            )

        for name, source in (
            ("observation_age_ms", path),
            ("kms_observation_age_ms", f"kms/{path}"),
            ("key_observation_age_ms", f"key/{path}"),
            ("sender_queue_observation_age_ms", f"sender_queue/{path}"),
            ("receiver_observation_age_ms", f"receiver/{path}"),
            ("device_observation_age_ms", f"device/{path}"),
            ("bc_observation_age_ms", f"clock/{path}"),
        ):
            value = row.get(name)

            if value is not None and not fresh(value):
                add(
                    "telemetry_stale",
                    source,
                    [measurement(name, value, "ms", max(0, value), source)],
                    ["stale_observation"],
                    ["monitor_gap", "delivery_delay", "source_stopped"],
                    status="insufficient_evidence",
                )

    if len(slow) == 2:
        add(
            "branch_degradation",
            "A+B",
            sum(branch_measurements.values(), []),
            ["both_rtt_high"]
            if all("rtt_high" in e for e in branch_evidence.values())
            else ["both_paths_degraded"],
            ["shared_segment", "independent_path_problems", "shared_endpoint"],
            status="candidate",
            cause="shared_or_parallel_slowdown",
            known_shared_segment=shared_segment,
        )
    else:
        for path in slow:
            add(
                "branch_degradation",
                path,
                branch_measurements[path],
                branch_evidence[path],
                ["path_congestion", "link_loss", "remote_endpoint"],
            )

    if not findings:
        observed = all(
            fresh(row.get("observation_age_ms")) for row in snapshot.values()
        )
        add(
            "no_anomaly_observed" if observed else "telemetry_missing",
            "system",
            [],
            [],
            ["unobserved_network_segment", "receiver"],
            status="normal" if observed else "insufficient_evidence",
        )

    return findings
