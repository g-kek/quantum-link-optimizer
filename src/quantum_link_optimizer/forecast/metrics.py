"""Accuracy of the RTT band and of breach warnings, per horizon."""

from collections import defaultdict

import numpy as np

from .features import trailing


def auc(probability, outcome):
    positive, negative = probability[outcome == 1], probability[outcome == 0]

    if not len(positive) or not len(negative):
        return None

    order = np.argsort(np.r_[positive, negative], kind="stable")
    ranks = np.empty(len(order))
    ranks[order] = np.arange(1, len(order) + 1)
    return float(
        (ranks[: len(positive)].sum() - len(positive) * (len(positive) + 1) / 2)
        / (len(positive) * len(negative))
    )


def band_metrics(prediction, actual):
    known = np.isfinite(actual) & np.isfinite(prediction.median)
    a = actual[known]
    low, median, high = (
        v[known] for v in (prediction.low, prediction.median, prediction.high)
    )
    return {
        "samples": int(known.sum()),
        "median_abs_error_ms": float(np.median(np.abs(median - a))),
        "coverage_80": float(np.mean((a >= low) & (a <= high))),
        "band_width_ms": float(np.median(high - low)),
    }


def breach_metrics(probability, outcome, threshold=0.5):
    known = np.isfinite(outcome) & np.isfinite(probability)
    p, y = probability[known], outcome[known].astype(int)
    alarm = p >= threshold
    hits = int(np.sum(alarm & (y == 1)))
    return {
        "samples": int(known.sum()),
        "base_rate": float(y.mean()) if len(y) else None,
        "brier": float(np.mean((p - y) ** 2)) if len(y) else None,
        "auc": auc(p, y),
        "precision": hits / alarm.sum() if alarm.sum() else None,
        "recall": hits / (y == 1).sum() if (y == 1).sum() else None,
    }


def best_threshold(probability, outcome):
    """Alarm level with the best F1 on validation, applied unchanged to test."""
    known = np.isfinite(outcome) & np.isfinite(probability)
    p, y = probability[known], outcome[known].astype(bool)
    best, level = -1.0, 0.5

    for candidate in np.unique(np.quantile(p, np.linspace(0.5, 0.999, 60))):
        alarm = p >= candidate
        hits = np.sum(alarm & y)
        f1 = 2 * hits / (alarm.sum() + y.sum()) if alarm.sum() + y.sum() else 0.0

        if f1 > best:
            best, level = f1, float(candidate)

    return level


def rising(flags):
    flags = np.asarray(flags, dtype=bool)
    return np.flatnonzero(flags[1:] & ~flags[:-1]) + 1


def hysteresis(probability, on, off):
    alarm = np.zeros(len(probability), dtype=bool)
    state = False

    for index, value in enumerate(np.nan_to_num(probability)):
        state = value >= on if not state else value >= off
        alarm[index] = state

    return alarm


def warnings(probability, rtt, episode, horizon, step_s, target_ms, threshold=0.5):
    """Lead time before each breach onset and alarms that led nowhere.

    A breach starts when the median RTT of the last minute rises above the
    target. The alarm switches on at the threshold and off below half of it.
    A warning counts if the alarm was on within the horizon before the onset
    and stayed on until it. An alarm episode with no onset during it or one
    horizon after it is false
    """
    minute = max(1, 60 // step_s)
    current = np.nan_to_num(trailing(rtt, minute, np.nanmedian)) > target_ms
    alarm = hysteresis(probability, threshold, threshold / 2)
    leads = defaultdict(list)
    missed = defaultdict(int)

    for onset in rising(current):
        kind = episode[onset] or "background"
        start = max(0, onset - horizon)

        if alarm[start:onset].size and alarm[onset - 1]:
            first = onset - 1

            while first > start and alarm[first - 1]:
                first -= 1

            leads[kind].append((onset - first) * step_s)
        else:
            missed[kind] += 1

    false_alarms = 0
    starts = rising(alarm)
    ends = rising(~alarm)

    for begin in starts:
        later = ends[ends > begin]
        end = later[0] if len(later) else len(alarm)

        if current[max(0, begin - 1)]:
            continue

        if not current[begin : end + horizon + 1].any():
            false_alarms += 1

    days = len(rtt) * step_s / 86400
    kinds = sorted(set(leads) | set(missed))
    return {
        "onsets": sum(len(v) for v in leads.values()) + sum(missed.values()),
        "warned": sum(len(v) for v in leads.values()),
        "median_lead_s": float(np.median([x for v in leads.values() for x in v]))
        if leads
        else None,
        "alarm_episodes": len(starts),
        "false_alarms_per_day": false_alarms / days if days else None,
        "by_episode": {
            kind: {
                "onsets": len(leads[kind]) + missed[kind],
                "warned": len(leads[kind]),
                "median_lead_s": float(np.median(leads[kind])) if leads[kind] else None,
            }
            for kind in kinds
        },
    }
