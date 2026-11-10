"""Small robust statistics used by the RTT decomposition."""

from collections import deque

import numpy as np


def quantile(values, q):
    values = np.asarray(values, dtype=float)

    if not len(values):
        return None

    return float(np.quantile(values, q))


def mad(values):
    values = np.asarray(values, dtype=float)

    if not len(values):
        return None

    return float(np.median(np.abs(values - np.median(values))))


def sliding_min(times, values, window_s):
    """Minimum over the trailing window, the usual estimate of the base RTT."""
    result = np.empty(len(values))
    window = deque()

    for index, (moment, value) in enumerate(zip(times, values, strict=True)):
        while window and values[window[-1]] >= value:
            window.pop()

        window.append(index)

        while times[window[0]] < moment - window_s:
            window.popleft()

        result[index] = values[window[0]]

    return result


def change_points(values, min_size=8, threshold=6.0, limit=8):
    """Binary segmentation for level shifts, returns split indices."""
    values = np.asarray(values, dtype=float)

    if len(values) < 2 * min_size:
        return []

    noise = mad(np.diff(values)) or 0.0
    scale = max(noise * 1.4826 / np.sqrt(2), 1e-9)
    found = []
    segments = [(0, len(values))]

    while segments and len(found) < limit:
        start, end = segments.pop()
        segment = values[start:end]
        size = len(segment)

        if size < 2 * min_size:
            continue

        cumulative = np.cumsum(segment)
        left = np.arange(min_size, size - min_size + 1)
        right = size - left
        mean_left = cumulative[left - 1] / left
        mean_right = (cumulative[-1] - cumulative[left - 1]) / right
        scores = np.abs(mean_left - mean_right) * np.sqrt(left * right / size)
        best = int(np.argmax(scores))
        split = int(left[best])

        if scores[best] / scale < threshold:
            continue

        found.append(start + split)
        segments.extend([(start, start + split), (start + split, end)])

    return sorted(found)


def two_modes(values, min_weight=0.1, min_gap=10.0):
    """Otsu split of the RTT distribution, None when it is not clearly bimodal."""
    values = np.sort(np.asarray(values, dtype=float))

    if len(values) < 20:
        return None

    total_var = float(np.var(values))

    if total_var <= 0:
        return None

    count = len(values)
    cumulative = np.cumsum(values)
    index = np.arange(1, count)
    weight = index / count
    low_mean = cumulative[index - 1] / index
    high_mean = (cumulative[-1] - cumulative[index - 1]) / (count - index)
    between = weight * (1 - weight) * (high_mean - low_mean) ** 2
    between[(weight < min_weight) | (weight > 1 - min_weight)] = 0

    split = int(index[np.argmax(between)])
    low, high = values[:split], values[split:]
    separation = float(between.max()) / total_var

    if separation < 0.8 or np.median(high) - np.median(low) < min_gap:
        return None

    gap = float(np.median(high) - np.median(low))
    return {
        "low_ms": float(np.median(low)),
        "high_ms": float(np.median(high)),
        "high_share": len(high) / len(values),
        "separation": separation,
        "tight": max(mad(low), mad(high)) < 0.1 * gap,
    }


def ranks(values):
    order = np.argsort(values, kind="stable")
    result = np.empty(len(values))
    result[order] = np.arange(len(values))
    return result


def spearman(first, second):
    first, second = np.asarray(first, float), np.asarray(second, float)

    if len(first) < 10 or np.ptp(first) == 0 or np.ptp(second) == 0:
        return None

    return float(np.corrcoef(ranks(first), ranks(second))[0, 1])


def line_fit(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)

    if len(np.unique(x)) < 2:
        return None

    slope, intercept = np.polyfit(x, y, 1)
    residual = y - (slope * x + intercept)
    return {
        "slope": float(slope),
        "intercept": float(intercept),
        "residual_ms": float(np.sqrt(np.mean(residual**2))),
    }
