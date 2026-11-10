"""Diagnose a ping CSV or the built-in examples and write JSON plus HTML."""

import hashlib
import json
from importlib.resources import files
from pathlib import Path

from quantum_link_optimizer.artifacts import dump_json, runtime_metadata

from . import examples
from .causes import diagnose
from .decomposition import decompose
from .probes import read_probes, write_probes
from .segments import segments
from .waterfall import breakdown


def analyse(probes, target_ms=100.0, distance_km=None, order=None, window_s=600.0):
    decomposition = decompose(probes, target_ms, window_s, distance_km)
    route = segments(probes, order)
    return {
        "kind": "rtt_diagnosis",
        "runtime": runtime_metadata(),
        "sources": sorted({p.source for p in probes}),
        "probes": len(probes),
        "decomposition": decomposition,
        "segments": route,
        "diagnosis": diagnose(decomposition, route, target_ms),
    }


def render(report, output: Path, name="index.html"):
    folder = files("quantum_link_optimizer").joinpath("templates/analysis")
    payload = json.dumps(report, ensure_ascii=False, allow_nan=False)
    payload = payload.replace("<", "\\u003c").replace("&", "\\u0026")
    document = (
        folder.joinpath("diagnosis.html")
        .read_text(encoding="utf-8")
        .replace("__STYLE__", folder.joinpath("diagnosis.css").read_text("utf-8"))
        .replace("__SCRIPT__", folder.joinpath("diagnosis.js").read_text("utf-8"))
        .replace("__DATA__", payload)
    )
    output.mkdir(parents=True, exist_ok=True)
    path = output / name
    path.write_text(document, encoding="utf-8")
    return path


def diagnose_csv(source: Path, output: Path, **options):
    report = analyse(read_probes(source), **options)
    report["input"] = {
        "file": source.name,
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    }
    report["title"] = "Разбор RTT двух путей"
    output.mkdir(parents=True, exist_ok=True)
    dump_json(output / "diagnosis.json", report)
    render(report, output)
    return report


def run_examples(output: Path, distance_km=1200.0):
    rows = []

    for name, expected in examples.EXPECTED.items():
        built = examples.build(name)
        write_probes(output / "examples" / f"{name}.csv", built)
        report = analyse([probe for probe, _ in built], distance_km=distance_km)
        report["title"] = f"Пример: {examples.DESCRIPTIONS[name]}"
        report["example"] = {"name": name, "expected": expected}
        render(report, output / "examples", f"{name}.html")
        verdict = report["diagnosis"]["verdict"]
        where = (verdict.get("where") or {}).get("segment")
        matched = verdict["cause"] == expected["cause"]

        if "scope" in expected:
            matched = matched and verdict.get("scope") == expected["scope"]

        if "segment" in expected:
            matched = matched and where == expected["segment"]

        rows.append(
            {
                "name": name,
                "description": examples.DESCRIPTIONS[name],
                "expected": expected,
                "found": {
                    "cause": verdict["cause"],
                    "title": verdict["title"],
                    "scope": verdict.get("scope"),
                    "segment": where,
                    "score": verdict.get("score"),
                },
                "matched": matched,
                "page": f"examples/{name}.html",
            }
        )

    summary = {
        "kind": "rtt_diagnosis_examples",
        "title": "Разбор RTT на примерах с известной причиной",
        "runtime": runtime_metadata(),
        "examples": rows,
        "matched": sum(row["matched"] for row in rows),
        "total": len(rows),
        "scope": (
            "Синтетические примеры механизмов, совпадение показывает работу правил, "
            "а не точность на сети оператора"
        ),
    }
    dump_json(output / "examples.json", summary)
    render(summary, output)
    return summary


def breakdown_trial(trial: Path, output: Path, target_ms=100.0):
    result = breakdown(trial, target_ms)

    if result is None:
        raise ValueError("trial has no completed commands")

    output.mkdir(parents=True, exist_ok=True)
    dump_json(output / "breakdown.json", result)
    return result
