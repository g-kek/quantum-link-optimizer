"""Segment delay from probes to intermediate points along the same path.

The delay of a segment is the RTT to its far end minus the RTT to its near
end, as in RIPE Atlas / IHR delay change detection. Absolute differences can
include return path effects, so changes over time matter more than levels
"""

from collections import Counter, defaultdict

import numpy as np

from . import stats


def target_series(probes, path):
    rows = [p for p in probes if p.path == path and p.rtt_ms is not None]

    if not rows:
        return {}

    probe = Counter(p.probe for p in rows).most_common(1)[0][0]
    sizes = sorted({p.packet_bytes for p in rows if p.packet_bytes is not None})
    size = sizes[0] if sizes else None
    series = defaultdict(dict)

    for p in rows:
        if p.probe == probe and p.packet_bytes == size:
            series[p.target][p.sample] = p.rtt_ms

    return {name: values for name, values in series.items() if len(values) >= 5}


def ordered_targets(series, order=None):
    if order:
        return [name for name in order if name in series]

    def floor(name):
        return stats.quantile(list(series[name].values()), 0.1)

    return sorted(series, key=floor)


def segment_steps(near, far):
    common = sorted(set(near) & set(far))

    if len(common) < 50:
        return []

    difference = np.array([far[s] - near[s] for s in common])
    blocks = min(100, len(difference) // 5)
    edges = np.linspace(0, len(difference), blocks + 1).astype(int)
    levels = np.array(
        [
            np.quantile(difference[a:b], 0.1)
            for a, b in zip(edges, edges[1:], strict=False)
        ]
    )
    steps = []

    for split in stats.change_points(levels, min_size=max(3, blocks // 25)):
        before = float(np.median(levels[:split]))
        after = float(np.median(levels[split:]))

        if abs(after - before) >= 5.0:
            steps.append(
                {
                    "sample": int(common[edges[split]]),
                    "before_ms": before,
                    "after_ms": after,
                }
            )

    return steps


def segments(probes, order=None):
    result = {}

    for path in ("A", "B"):
        series = target_series(probes, path)

        if len(series) < 2:
            continue

        names = ordered_targets(series, order)
        rows = []
        previous = None

        for name in names:
            values = list(series[name].values())
            floor = stats.quantile(values, 0.1)
            median = float(np.median(values))
            row = {
                "to": name,
                "from": previous["to"] if previous else "sender",
                "floor_ms": floor,
                "variable_ms": median - floor,
                "added_floor_ms": floor - (previous["floor_ms"] if previous else 0.0),
                "added_variable_ms": (median - floor)
                - (previous["variable_ms"] if previous else 0.0),
                "steps": segment_steps(series[previous["to"]], series[name])
                if previous
                else [],
            }
            rows.append(row)
            previous = row

        worst = max(
            rows, key=lambda row: row["added_floor_ms"] + row["added_variable_ms"]
        )
        result[path] = {"segments": rows, "largest": f"{worst['from']} → {worst['to']}"}

    return result
