"""Split measured RTT into a stable base, queueing, size and probe effects."""

from collections import Counter, defaultdict

import numpy as np

from . import stats

FIBER_MS_PER_KM = 0.005
ROUTE_FACTOR = 1.4
MIN_SAMPLES = 5


def pick_series(probes, path):
    """Main series of a path: farthest target, most common probe, smallest size."""
    rows = [p for p in probes if p.path == path]

    if not rows:
        return None

    targets = defaultdict(list)

    for row in rows:
        if row.rtt_ms is not None:
            targets[row.target].append(row.rtt_ms)

    if "endpoint" in targets:
        target = "endpoint"
    elif targets:
        target = max(targets, key=lambda name: np.median(targets[name]))
    else:
        target = rows[0].target

    rows = [p for p in rows if p.target == target]
    kinds = Counter(p.probe for p in rows)

    # The reported 700 ms came from ping, keep ICMP as the reference series
    probe = "icmp" if "icmp" in kinds else kinds.most_common(1)[0][0]
    rows = [p for p in rows if p.probe == probe]
    sizes = sorted({p.packet_bytes for p in rows if p.packet_bytes is not None})

    if sizes:
        rows = [p for p in rows if p.packet_bytes == sizes[0]]

    return {
        "path": path,
        "target": target,
        "probe": probe,
        "packet_bytes": sizes[0] if sizes else None,
        "rows": rows,
    }


def base_window(times, window_s):
    if len(times) < 2:
        return window_s

    interval = float(np.median(np.diff(times)))
    return max(window_s, 10 * interval)


def time_shape(times, rtt, base, target_ms):
    """Level shifts of the base, daily profile and two-mode behaviour."""
    result = {"steps": [], "daily": None, "modes": None}
    blocks = min(200, len(rtt) // 5)

    if blocks >= 10:
        # Load moves the queue, a new route moves the base
        edges = np.linspace(0, len(base), blocks + 1).astype(int)
        levels = np.array(
            [np.median(base[a:b]) for a, b in zip(edges, edges[1:], strict=False)]
        )
        splits = stats.change_points(levels, min_size=max(3, blocks // 25))
        bounds = [0, *splits, blocks]

        for left, split, right in zip(bounds, bounds[1:], bounds[2:], strict=False):
            before = float(np.median(levels[left:split]))
            after = float(np.median(levels[split:right]))

            if abs(after - before) >= max(10.0, 0.2 * min(before, after)):
                result["steps"].append(
                    {
                        "time_s": float(times[edges[split]]),
                        "before_ms": before,
                        "after_ms": after,
                    }
                )

    if len(times) and times[-1] - times[0] >= 20 * 3600:
        hours = (times % 86400 // 3600).astype(int)
        profile = {
            int(hour): float(np.median(rtt[hours == hour]))
            for hour in np.unique(hours)
            if np.sum(hours == hour) >= 3
        }

        if len(profile) >= 12:
            low, high = min(profile.values()), max(profile.values())
            result["daily"] = {
                "median_by_utc_hour_ms": profile,
                "peak_hour_utc": max(profile, key=profile.get),
                "quiet_hour_utc": min(profile, key=profile.get),
                "peak_to_quiet_ratio": high / low if low > 0 else None,
                "peak_above_target": high > target_ms,
                "quiet_above_target": low > target_ms,
            }

    sample = rtt[:: max(1, len(rtt) // 50000)]
    modes = stats.two_modes(sample)

    if modes is not None:
        threshold = (modes["low_ms"] + modes["high_ms"]) / 2
        high = rtt > threshold
        span_h = max((times[-1] - times[0]) / 3600, 1 / 60)
        modes["switches_per_hour"] = float(np.sum(high[1:] != high[:-1]) / span_h)
        result["modes"] = modes

    return result


def path_summary(series, target_ms, window_s):
    rows = series["rows"]
    ok = [p for p in rows if p.rtt_ms is not None]
    lost = sum(p.status == "timeout" for p in rows)
    summary = {
        "path": series["path"],
        "target": series["target"],
        "probe": series["probe"],
        "packet_bytes": series["packet_bytes"],
        "probes": len(rows),
        "answered": len(ok),
        "loss_rate": lost / len(rows) if rows else None,
    }

    if len(ok) < MIN_SAMPLES:
        return summary, None

    times = np.array([p.time_s for p in ok])
    rtt = np.array([p.rtt_ms for p in ok])
    base = stats.sliding_min(times, rtt, base_window(times, window_s))
    queue = rtt - base
    summary.update(
        min_ms=float(rtt.min()),
        median_ms=float(np.median(rtt)),
        p95_ms=stats.quantile(rtt, 0.95),
        p99_ms=stats.quantile(rtt, 0.99),
        base_ms=float(np.median(base)),
        queue_median_ms=float(np.median(queue)),
        queue_p95_ms=stats.quantile(queue, 0.95),
        queue_share=float(np.median(queue) / np.median(rtt)),
        jitter_ms=float(np.median(np.abs(np.diff(rtt)))) if len(rtt) > 1 else 0.0,
        above_target_share=float(np.mean(rtt > target_ms)),
        base_above_target=bool(np.median(base) > target_ms),
        shape=time_shape(times, rtt, base, target_ms),
    )
    detail = {
        "times": times,
        "rtt": rtt,
        "base": base,
        "queue": queue,
        "samples": np.array([p.sample for p in ok]),
        "load": np.array(
            [np.nan if p.load_percent is None else p.load_percent for p in ok]
        ),
    }
    return summary, detail


def size_sweep(probes, series):
    groups = defaultdict(list)

    for p in probes:
        if (
            p.path == series["path"]
            and p.target == series["target"]
            and p.probe == series["probe"]
            and p.packet_bytes is not None
            and p.rtt_ms is not None
        ):
            groups[p.packet_bytes].append(p.rtt_ms)

    sizes = sorted(size for size, values in groups.items() if len(values) >= 5)

    if len(sizes) < 2:
        return None

    floors = [stats.quantile(groups[size], 0.1) for size in sizes]
    fit = stats.line_fit(sizes, floors)
    spread = fit["slope"] * (sizes[-1] - sizes[0])
    significant = fit["slope"] > 0 and spread > max(2.0, 3 * fit["residual_ms"])
    result = {
        "sizes_bytes": sizes,
        "floor_ms_by_size": dict(zip(map(str, sizes), floors, strict=True)),
        "slope_ms_per_byte": fit["slope"],
        "significant": bool(significant),
        "serialization_ms_largest": spread if significant else 0.0,
    }

    if significant:
        # Echo request and reply both cross the narrowest link
        result["bottleneck_kbit_s"] = 16 / fit["slope"]

    return result


def probe_types(probes, series):
    groups = defaultdict(list)

    for p in probes:
        if (
            p.path == series["path"]
            and p.target == series["target"]
            and p.rtt_ms is not None
            and p.packet_bytes in (None, series["packet_bytes"])
        ):
            groups[p.probe].append(p.rtt_ms)

    groups = {name: values for name, values in groups.items() if len(values) >= 5}

    if len(groups) < 2 or "icmp" not in groups:
        return None

    floors = {name: stats.quantile(values, 0.1) for name, values in groups.items()}
    medians = {name: float(np.median(values)) for name, values in groups.items()}
    other = min(value for name, value in floors.items() if name != "icmp")
    return {
        "floor_ms": floors,
        "median_ms": medians,
        "icmp_excess_ms": floors["icmp"] - other,
    }


def load_effect(detail):
    if detail is None:
        return None

    known = ~np.isnan(detail["load"])

    if known.sum() < 10:
        return None

    load, queue = detail["load"][known], detail["queue"][known]
    busy, quiet = queue[load >= 70], queue[load <= 40]
    return {
        "samples": int(known.sum()),
        "spearman_load_queue": stats.spearman(load, queue),
        "queue_busy_ms": float(np.median(busy)) if len(busy) else None,
        "queue_quiet_ms": float(np.median(quiet)) if len(quiet) else None,
    }


def shared_excess(details):
    if details.get("A") is None or details.get("B") is None:
        return None

    first = dict(zip(details["A"]["samples"], details["A"]["queue"], strict=True))
    second = dict(zip(details["B"]["samples"], details["B"]["queue"], strict=True))
    common = sorted(set(first) & set(second))

    if len(common) < 10:
        return None

    return {
        "paired_samples": len(common),
        "spearman_queue": stats.spearman(
            [first[s] for s in common], [second[s] for s in common]
        ),
    }


def latency_budget(summary, sweep, probe_cmp, distance_km):
    """Approximate split of the median RTT, the parts are estimates, not spans."""
    if "median_ms" not in summary:
        return None

    constant = summary["base_ms"]
    parts = []

    if distance_km:
        propagation = min(constant, 2 * distance_km * ROUTE_FACTOR * FIBER_MS_PER_KM)
        parts.append(("propagation", "Распространение, оценка", propagation))
        constant -= propagation

    if sweep and sweep["significant"] and summary["packet_bytes"]:
        serialization = min(
            constant, sweep["slope_ms_per_byte"] * summary["packet_bytes"]
        )
        parts.append(("serialization", "Сериализация", serialization))
        constant -= serialization

    if probe_cmp and probe_cmp["icmp_excess_ms"] > 0 and summary["probe"] == "icmp":
        icmp = min(constant, probe_cmp["icmp_excess_ms"])
        parts.append(("icmp_processing", "Обработка ICMP", icmp))
        constant -= icmp

    parts.append(("other_constant", "Прочая постоянная часть", max(constant, 0.0)))
    parts.append(("queue", "Очередь", summary["queue_median_ms"]))
    return [{"id": key, "label": label, "ms": value} for key, label, value in parts]


def decompose(probes, target_ms=100.0, window_s=600.0, distance_km=None):
    paths, details, chart = {}, {}, {}

    for path in ("A", "B"):
        series = pick_series(probes, path)

        if series is None:
            continue

        summary, detail = path_summary(series, target_ms, window_s)
        sweep = size_sweep(probes, series)
        probe_cmp = probe_types(probes, series)
        summary.update(
            size_sweep=sweep,
            probe_types=probe_cmp,
            load=load_effect(detail),
            budget=latency_budget(summary, sweep, probe_cmp, distance_km),
        )
        paths[path] = summary
        details[path] = detail

        if detail is not None:
            step = max(1, len(detail["times"]) // 1500)
            chart[path] = {
                "time_s": detail["times"][::step].round(3).tolist(),
                "rtt_ms": detail["rtt"][::step].round(3).tolist(),
                "base_ms": detail["base"][::step].round(3).tolist(),
            }

    return {
        "paths": paths,
        "shared": shared_excess(details),
        "chart": chart,
        "settings": {
            "target_ms": target_ms,
            "base_window_s": window_s,
            "distance_km": distance_km,
        },
    }
