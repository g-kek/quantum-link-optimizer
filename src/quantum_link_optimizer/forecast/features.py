"""Features at origin t use samples up to t only, targets lie after t."""

from dataclasses import dataclass

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

DAY_S = 24 * 3600


@dataclass
class Frame:
    names: list
    x: np.ndarray
    value: np.ndarray
    breach: np.ndarray
    origin: np.ndarray
    path: np.ndarray
    horizons: tuple
    target_ms: float
    step_s: int


def trailing(values, window, reducer):
    """Reducer over the last `window` samples, NaN until the window fills."""
    result = np.full(len(values), np.nan)

    if len(values) < window:
        return result

    view = sliding_window_view(values, window)

    with np.errstate(all="ignore"):
        result[window - 1 :] = reducer(view, axis=1)

    return result


def lagged(values, lag):
    result = np.full(len(values), np.nan)

    if 0 < lag < len(values):
        result[lag:] = values[:-lag]

    return result


def path_features(rtt, load, loss, keys, time_s, step_s, horizons):
    minute = max(1, 60 // step_s)
    columns = {
        "rtt_last": rtt,
        "rtt_median_1m": trailing(rtt, minute, np.nanmedian),
        "rtt_median_5m": trailing(rtt, 5 * minute, np.nanmedian),
        "rtt_median_15m": trailing(rtt, 15 * minute, np.nanmedian),
        "rtt_p90_5m": trailing(
            rtt, 5 * minute, lambda v, axis: np.nanquantile(v, 0.9, axis=axis)
        ),
        "rtt_min_15m": trailing(rtt, 15 * minute, np.nanmin),
        "load": load,
        "loss": loss,
        "keys": keys,
    }
    columns["rtt_trend_5m"] = columns["rtt_median_1m"] - lagged(
        columns["rtt_median_1m"], 5 * minute
    )
    columns["load_trend_15m"] = load - lagged(load, 15 * minute)
    columns["loss_trend_15m"] = loss - lagged(loss, 15 * minute)
    drain = (lagged(keys, 5 * minute) - keys) / (5 * minute * step_s)

    with np.errstate(all="ignore"):
        exhaustion = np.where(drain > 0, keys / drain, np.inf)

    columns["keys_exhaustion_log_s"] = np.log1p(np.minimum(exhaustion, 7 * DAY_S))
    hour = (time_s % DAY_S) / 3600
    columns["hour_sin"] = np.sin(hour / 24 * 2 * np.pi)
    columns["hour_cos"] = np.cos(hour / 24 * 2 * np.pi)
    columns["weekend"] = (((time_s // DAY_S) + 3) % 7 >= 5).astype(float)
    day = DAY_S // step_s

    for h in horizons:
        columns[f"rtt_yesterday_h{h}"] = lagged(rtt, day - h)

    return columns


def minute_median(rtt, step_s):
    return trailing(rtt, max(1, 60 // step_s), np.nanmedian)


def future(rtt, horizons, target_ms, step_s=60):
    """Value at t+h and whether a minute median within (t, t+h] exceeds the target.

    The breach uses the same minute median that marks the onset of a breach,
    so a warning and the event it warns about are defined the same way
    """
    values, breach = [], []
    level = minute_median(rtt, step_s)

    for h in horizons:
        values.append(np.r_[rtt[h:], np.full(h, np.nan)])
        window = np.full(len(rtt), np.nan)

        if len(rtt) > h:
            view = sliding_window_view(level[1:], h)

            with np.errstate(all="ignore"):
                window[: len(view)] = np.nanmax(view, axis=1)

        breach.append(np.where(np.isnan(window), np.nan, window > target_ms))

    return np.stack(values, axis=1), np.stack(breach, axis=1)


def build(series, horizons, target_ms, step_s):
    """series: path -> dict of rtt, load, loss, keys and time_s arrays."""
    rows, names = [], None

    for index, (path, data) in enumerate(sorted(series.items())):
        columns = path_features(
            data["rtt"],
            data["load"],
            data["loss"],
            data["keys"],
            data["time_s"],
            step_s,
            horizons,
        )
        columns["path_index"] = np.full(len(data["rtt"]), float(index))
        names = list(columns)
        value, breach = future(data["rtt"], horizons, target_ms, step_s)
        rows.append(
            (
                np.column_stack([columns[name] for name in names]),
                value,
                breach,
                np.arange(len(data["rtt"])),
                np.full(len(data["rtt"]), path),
            )
        )

    return Frame(
        names=names,
        x=np.concatenate([r[0] for r in rows]),
        value=np.concatenate([r[1] for r in rows]),
        breach=np.concatenate([r[2] for r in rows]),
        origin=np.concatenate([r[3] for r in rows]),
        path=np.concatenate([r[4] for r in rows]),
        horizons=tuple(horizons),
        target_ms=target_ms,
        step_s=step_s,
    )


class Scaler:
    """Medians fill gaps, missing flags keep the gap visible to the model."""

    def __init__(self, x):
        with np.errstate(all="ignore"):
            finite = np.where(np.isfinite(x), x, np.nan)
            self.fill = np.nan_to_num(np.nanmedian(finite, axis=0))
            self.mean = np.nan_to_num(np.nanmean(finite, axis=0))
            self.scale = np.nan_to_num(np.nanstd(finite, axis=0), nan=1.0)

        self.scale[self.scale < 1e-9] = 1.0
        self.missing = np.isnan(finite).any(axis=0)

    def transform(self, x):
        finite = np.where(np.isfinite(x), x, np.nan)
        flags = np.isnan(finite[:, self.missing]).astype(np.float32)
        filled = np.where(np.isnan(finite), self.fill, finite)
        scaled = (filled - self.mean) / self.scale
        return np.column_stack([scaled, flags]).astype(np.float32)
