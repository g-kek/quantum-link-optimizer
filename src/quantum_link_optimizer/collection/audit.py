from array import array


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    left = int(position)
    right = min(left + 1, len(ordered) - 1)
    return ordered[left] + (ordered[right] - ordered[left]) * (position - left)


def choices(pair):
    ewma = min(
        pair,
        key=lambda path: (
            pair[path]["features"]["ewma_rtt_ms"] is None,
            pair[path]["features"]["ewma_rtt_ms"] or 0,
            path,
        ),
    )
    oracle = min(
        pair,
        key=lambda path: (
            pair[path]["targets"]["rtt_ms"] is None,
            pair[path]["targets"]["rtt_ms"] or 0,
        ),
    )
    return {
        "fixed_a": "A",
        "fixed_b": "B",
        "causal_ewma": ewma,
        "oracle_bound_future_labels": oracle,
    }


class Audit:
    def __init__(self):
        self.policies = {
            name: {"decisions": 0, "observed": 0, "within": 0, "rtts": array("d")}
            for name in (
                "fixed_a",
                "fixed_b",
                "causal_ewma",
                "oracle_bound_future_labels",
            )
        }
        self.known = 0
        self.both_over = 0

    def add(self, pair):
        for name, path in choices(pair).items():
            state = self.policies[name]
            target = pair[path]["targets"]
            state["decisions"] += 1
            state["observed"] += target["probe_status"] != "missing"
            state["within"] += target["probe_within_target"] is True
            if target["rtt_ms"] is not None:
                state["rtts"].append(target["rtt_ms"])
        if all(row["targets"]["probe_status"] != "missing" for row in pair.values()):
            self.known += 1
            self.both_over += all(
                row["targets"]["probe_within_target"] is False for row in pair.values()
            )

    def result(self):
        result = {}
        for name, state in self.policies.items():
            rtts = state["rtts"]
            result[name] = {
                "decisions": state["decisions"],
                "observed": state["observed"],
                "within_target_fraction": state["within"] / state["decisions"]
                if state["decisions"]
                else None,
                "mean_rtt_ms_successful_probes": sum(rtts) / len(rtts)
                if rtts
                else None,
                "p95_rtt_ms_successful_probes": percentile(rtts, 0.95),
                "p99_rtt_ms_successful_probes": percentile(rtts, 0.99),
            }
        return result | {
            "fully_observed_pairs": self.known,
            "both_over_target_fraction": self.both_over / self.known
            if self.known
            else None,
            "interpretation": "probe replay only; not optimized command delivery",
        }
