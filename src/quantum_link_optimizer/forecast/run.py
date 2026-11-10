"""Train on the first days, calibrate on the next, score on the last ones."""

import hashlib
import json
from collections import defaultdict
from importlib.resources import files
from pathlib import Path

import numpy as np

from quantum_link_optimizer.artifacts import dump_json, runtime_metadata

from . import generator
from .features import Scaler, build
from .metrics import band_metrics, best_threshold, breach_metrics, warnings
from .models import Learned, PointBaseline, conformal, platt, recalibrate, widen

BASELINES = (
    ("persistence", "Последнее значение", "rtt_last"),
    ("median_5m", "Медиана 5 минут", "rtt_median_5m"),
    ("yesterday", "То же время вчера", "rtt_yesterday_h{h}"),
)


def synthetic_series(days, seed):
    week = generator.generate(days, seed)
    series = {}

    for index, path in enumerate(("A", "B")):
        series[path] = {
            "time_s": week.time_s,
            "rtt": week.rtt_ms[:, index],
            "load": week.load[:, index],
            "loss": week.loss[:, index],
            "keys": week.keys,
            "episode": week.episode[:, index],
        }

    return series, week.step_s


def csv_series(path):
    """Resample measured probes of each path onto one regular grid."""
    from quantum_link_optimizer.analysis.probes import read_probes

    probes = [p for p in read_probes(path) if p.target == "endpoint"]
    times = np.array(sorted({p.time_s for p in probes}))

    if len(times) < 100:
        raise ValueError("forecast needs at least 100 probe times")

    step = max(1, int(round(float(np.median(np.diff(times))))))
    start = times[0]
    size = int((times[-1] - start) // step) + 1
    series = {}

    for name in ("A", "B"):
        rtt = defaultdict(list)
        lost = defaultdict(int)
        total = defaultdict(int)
        load = defaultdict(list)

        for p in probes:
            if p.path != name:
                continue

            slot = int((p.time_s - start) // step)
            total[slot] += 1

            if p.rtt_ms is None:
                lost[slot] += p.status == "timeout"
            else:
                rtt[slot].append(p.rtt_ms)

            if p.load_percent is not None:
                load[slot].append(p.load_percent / 100)

        if not total:
            continue

        def grid(source, reducer):
            return np.array(
                [reducer(source[i]) if source.get(i) else np.nan for i in range(size)]
            )

        series[name] = {
            "time_s": start + np.arange(size) * step,
            "rtt": grid(rtt, np.median),
            "load": grid(load, np.mean),
            "loss": np.array(
                [lost[i] / total[i] if total.get(i) else np.nan for i in range(size)]
            ),
            "keys": np.full(size, np.nan),
            "episode": np.full(size, "", dtype=object),
        }

    return series, step


def splits(frame, purge):
    """Chronological split by origin, a gap of one horizon at each border."""
    last = frame.origin.max() + 1
    first_cut, second_cut = int(last * 0.6), int(last * 0.8)
    known = np.isfinite(frame.value).all(axis=1)
    train = known & (frame.origin < first_cut - purge)
    validation = (
        known & (frame.origin >= first_cut) & (frame.origin < second_cut - purge)
    )
    test = known & (frame.origin >= second_cut)
    return np.flatnonzero(train), np.flatnonzero(validation), np.flatnonzero(test)


def calibrated(model, frame, validation, test):
    on_validation = model.predict(frame, validation)
    widths = conformal(on_validation, frame, validation)
    coefficients = [
        platt(p.probability, frame.breach[validation, i])
        for i, p in enumerate(on_validation)
    ]
    thresholds = [
        best_threshold(p.probability, frame.breach[validation, i])
        for i, p in enumerate(recalibrate(on_validation, coefficients))
    ]
    predictions = recalibrate(widen(model.predict(frame, test), widths), coefficients)
    return predictions, thresholds


def score(predictions, thresholds, frame, rows, series):
    result = []

    for index, (h, prediction) in enumerate(
        zip(frame.horizons, predictions, strict=True)
    ):
        warn = {}

        for path, data in series.items():
            mask = frame.path[rows] == path
            probability = np.full(len(data["rtt"]), np.nan)
            probability[frame.origin[rows][mask]] = prediction.probability[mask]
            first = frame.origin[rows][mask].min()
            warn[path] = warnings(
                probability[first:],
                data["rtt"][first:],
                data["episode"][first:],
                h,
                frame.step_s,
                frame.target_ms,
                thresholds[index],
            )

        result.append(
            {
                "horizon_s": h * frame.step_s,
                "band": band_metrics(prediction, frame.value[rows, index]),
                "breach": breach_metrics(
                    prediction.probability, frame.breach[rows, index], thresholds[index]
                ),
                "alarm_threshold": thresholds[index],
                "warnings": warn,
            }
        )

    return result


def chart(prediction, frame, rows, series, h_index):
    """One day of the test period for the path with most breaches."""
    h = frame.horizons[h_index]
    day = 86400 // frame.step_s
    best = max(series, key=lambda p: np.nansum(series[p]["rtt"] > frame.target_ms))
    mask = frame.path[rows] == best
    origins = frame.origin[rows][mask]
    end = origins.max()
    keep = origins >= end - day
    step = max(1, keep.sum() // 1500)
    pick = np.flatnonzero(keep)[::step]
    target_index = origins[pick] + h
    data = series[best]

    def rounded(values):
        return [None if not np.isfinite(v) else round(float(v), 2) for v in values]

    return {
        "path": best,
        "horizon_s": h * frame.step_s,
        "time_s": rounded(data["time_s"][target_index]),
        "actual_ms": rounded(data["rtt"][target_index]),
        "low_ms": rounded(prediction.low[mask][pick]),
        "median_ms": rounded(prediction.median[mask][pick]),
        "high_ms": rounded(prediction.high[mask][pick]),
        "probability": rounded(prediction.probability[mask][pick]),
        "episode": [str(e) for e in data["episode"][target_index]],
    }


def render(report, output):
    folder = files("quantum_link_optimizer").joinpath("templates/forecast")
    payload = json.dumps(report, ensure_ascii=False, allow_nan=False)
    payload = payload.replace("<", "\\u003c").replace("&", "\\u0026")
    document = (
        folder.joinpath("forecast.html")
        .read_text(encoding="utf-8")
        .replace("__STYLE__", folder.joinpath("forecast.css").read_text("utf-8"))
        .replace("__SCRIPT__", folder.joinpath("forecast.js").read_text("utf-8"))
        .replace("__DATA__", payload)
    )
    (output / "index.html").write_text(document, encoding="utf-8")


def run_forecast(
    output: Path,
    source: Path | None = None,
    days=10,
    seed=42,
    target_ms=100.0,
    horizons_min=(1, 5, 15),
    epochs=40,
):
    if source is None:
        series, step_s = synthetic_series(days, seed)
        origin = {"kind": "synthetic", "days": days, "seed": seed}
    else:
        series, step_s = csv_series(source)
        origin = {
            "kind": "csv",
            "file": source.name,
            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        }

    horizons = tuple(max(1, int(m * 60 // step_s)) for m in horizons_min)
    frame = build(series, horizons, target_ms, step_s)
    train, validation, test = splits(frame, max(horizons))

    if min(len(train), len(validation), len(test)) == 0:
        raise ValueError("not enough data for train, validation and test")

    scaler = Scaler(frame.x[train])
    models = {
        "model": (
            "Квантильная сеть",
            Learned(epochs=epochs, seed=seed).fit(frame, train, validation, scaler),
        )
    }

    for name, title, column in BASELINES:
        models[name] = (title, PointBaseline(name, column).fit(frame, train))

    results, predictions = {}, {}

    for name, (title, model) in models.items():
        predictions[name], thresholds = calibrated(model, frame, validation, test)
        results[name] = {
            "title": title,
            "horizons": score(predictions[name], thresholds, frame, test, series),
        }

    report = {
        "kind": "rtt_forecast",
        "title": "Прогноз задержки и превышения цели",
        "runtime": runtime_metadata(),
        "source": origin,
        "target_ms": target_ms,
        "step_s": step_s,
        "horizons_s": [h * step_s for h in horizons],
        "rows": {"train": len(train), "validation": len(validation), "test": len(test)},
        "features": frame.names,
        "results": results,
        "training": models["model"][1].history,
        "chart": chart(predictions["model"][1], frame, test, series, 1)
        if len(horizons) > 1
        else chart(predictions["model"][0], frame, test, series, 0),
        "definitions": {
            "band": "Интервал p10–p90 RTT в момент t+h после конформной поправки",
            "breach": "Хотя бы одна минута в (t, t+h] с медианой RTT выше цели",
            "warning": (
                "Порог тревоги выбран по F1 на validation, тревога засчитана, "
                "если включилась до начала превышения в пределах горизонта "
                "и держалась до него"
            ),
        },
        "scope": (
            "Синтетика с заданными механизмами или CSV оператора, "
            "выводы о сети делаются только по реальным данным"
        ),
    }
    output.mkdir(parents=True, exist_ok=True)
    dump_json(output / "forecast-report.json", report)
    render(report, output)
    return report
