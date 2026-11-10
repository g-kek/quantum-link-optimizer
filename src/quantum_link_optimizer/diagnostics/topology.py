"""Dependency graph of the A/B service and localisation over it.

Each observable (what the sender can measure) depends on components. The
component that explains most anomalous observables without predicting
anomalies on healthy ones is the best candidate. Two observations rarely
separate a shared segment from the endpoints, so ties are reported as such
"""

COMPONENTS = {
    "segment:A": "Участок пути A",
    "segment:B": "Участок пути B",
    "shared": "Общий участок A и B",
    "sender": "Отправитель",
    "receiver": "Получатель",
    "kms": "KMS",
    "clock:A": "Часы на пути A",
    "clock:B": "Часы на пути B",
}


def graph(shared_segment=False):
    """Observable -> components it depends on."""
    paths = {}

    for path in ("A", "B"):
        parts = [f"segment:{path}", "sender", "receiver"]

        if shared_segment:
            parts.append("shared")

        paths[f"path:{path}"] = parts
        paths[f"keys:{path}"] = ["kms"]
        paths[f"clock:{path}"] = [f"clock:{path}"]

    return {
        **paths,
        "sender_queue": ["sender"],
        "receiver_spans": ["receiver"],
        "kms_inventory": ["kms"],
    }


def observable(finding):
    state, scope = finding["state"], finding["scope"]

    if state in {"branch_degradation", "path_change"}:
        return ["path:A", "path:B"] if scope == "A+B" else [f"path:{scope}"]

    if state == "kms_delay":
        return [f"keys:{scope}"]

    if state == "key_pool_empty":
        return ["kms_inventory"]

    if state == "local_overload":
        return ["sender_queue"]

    if state == "receiver_problem":
        return ["receiver_spans"]

    if state == "clock_unusable":
        return [f"clock:{scope}"]

    return []


def rank(anomalous, healthy, shared_segment=False):
    edges = graph(shared_segment)

    # Without a declared shared segment it is still a candidate, just unknown
    if not shared_segment:
        edges = {
            name: [*parts, "shared"] if name.startswith("path:") else parts
            for name, parts in edges.items()
        }

    scores = []

    for component, title in COMPONENTS.items():
        dependents = {name for name, parts in edges.items() if component in parts}

        if not dependents & anomalous:
            continue

        explained = len(dependents & anomalous) / len(anomalous)
        contradicted = len(dependents & healthy) / len(dependents)
        scores.append(
            {
                "component": component,
                "title": title,
                "score": round(explained - 0.5 * contradicted, 3),
                "explains": sorted(dependents & anomalous),
                "contradicted_by": sorted(dependents & healthy),
            }
        )

    return sorted(scores, key=lambda item: (-item["score"], len(item["explains"])))


def localize(findings, healthy=(), shared_segment=False):
    """Attach the most likely component to every finding with an observable."""
    anomalous = {name for finding in findings for name in observable(finding)}

    if not anomalous:
        return findings

    ranking = rank(anomalous, set(healthy) - anomalous, shared_segment)

    for finding in findings:
        names = set(observable(finding))
        candidates = [item for item in ranking if names & set(item["explains"])]

        if not candidates:
            continue

        best = candidates[0]["score"]
        tied = [item for item in candidates if best - item["score"] < 0.1]
        finding["location"] = {
            "component": tied[0]["component"] if len(tied) == 1 else None,
            "title": " или ".join(item["title"] for item in tied),
            "ambiguous": len(tied) > 1,
            "candidates": candidates[:4],
            "basis": "Граф зависимостей: какие наблюдения объясняет компонент",
        }

    return findings


def healthy_observables(snapshot, max_age_ms, findings):
    """Observables with fresh data and no finding, they argue against a cause."""
    healthy = set()

    for path, row in snapshot.items():
        age = row.get("observation_age_ms")

        if age is not None and 0 <= age <= max_age_ms:
            healthy.add(f"path:{path}")

        key_age = row.get("key_observation_age_ms")

        if key_age is not None and 0 <= key_age <= max_age_ms:
            healthy.add(f"keys:{path}")

    return healthy - {name for f in findings for name in observable(f)}
