import json
from collections import Counter, defaultdict
from contextlib import ExitStack

from quantum_link_optimizer.telemetry_lab.manifest import (
    begin,
    digest,
    finish,
    verify_files,
)
from quantum_link_optimizer.telemetry_lab.report import records

from .audit import Audit
from .features import FEATURE_NAMES, rows
from .preprocess import fit
from .series import load_series


def stream(path):
    with path.open() as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def sources_for(series, configs):
    sources, missing = [], []
    if len({c["episode_id"] for c in configs}) != len(configs):
        raise ValueError("duplicate episode identity")
    for config in configs:
        path = series / config["episode_id"]
        if not (path / "manifest.json").exists():
            missing.append(config["episode_id"])
            continue
        manifest = json.loads((path / "manifest.json").read_text())
        if manifest["status"] != "collected":
            missing.append(config["episode_id"])
            continue
        checks = verify_files(path)
        if not checks or not all(checks.values()):
            raise ValueError(f"source integrity failed: {path.name}")
        if json.loads((path / "scenario.json").read_text()) != config:
            raise ValueError("source scenario does not match series")
        sources.append((config, path))
    return sources, missing


def export_rows(sources, output, splits):
    quality = {
        "rows": Counter(),
        "episodes": Counter(),
        "probe_statuses": Counter(),
        "command_labels": 0,
    }
    audits = defaultdict(Audit)
    with ExitStack() as stack:
        handles = {
            split: stack.enter_context((output / f"{split}.jsonl").open("x"))
            for split in splits
        }
        for config, path in sources:
            events = records(path / "sender.jsonl")
            decisions = [
                event["sample"] for event in events if event["event"] == "decision"
            ]
            if sorted(decisions) != list(range(config["samples"])):
                raise ValueError("missing or duplicate decisions")
            pair = {}
            quality["episodes"][config["split"]] += 1
            for row in rows(config, events):
                handles[row["split"]].write(json.dumps(row) + "\n")
                quality["rows"][row["split"]] += 1
                quality["probe_statuses"][row["targets"]["probe_status"]] += 1
                quality["command_labels"] += (
                    row["targets"]["command_status"] is not None
                )
                pair[row["path"]] = row
                if len(pair) == 2:
                    audits[config["split"], config["profile"]].add(pair)
                    pair = {}
    results = defaultdict(dict)
    for (split, profile), audit in audits.items():
        results[split][profile] = audit.result()
    return quality, results


def build_dataset(series, output, allow_partial=False, frozen_preprocessing=None):
    definition = load_series(series)
    configs = definition["episodes"]
    if "final_test" not in definition["splits"] and "train" not in definition["splits"]:
        raise ValueError("development build requires train episodes for preprocessing")
    final = "final_test" in definition["splits"]
    if final and set(definition["splits"]) != {"final_test"}:
        raise ValueError("build final_test separately from training")
    if final and frozen_preprocessing is None:
        raise ValueError("final_test requires frozen training preprocessing")
    seeds = defaultdict(set)
    for config in configs:
        if config["split"] not in definition["splits"]:
            raise ValueError("episode split is outside selected splits")
        seeds[config["split"]].add(config["seed"])
    for split, values in seeds.items():
        if any(
            values.intersection(other) for name, other in seeds.items() if name != split
        ):
            raise ValueError("seed leakage between splits")
    targets = {config["target_ms"] for config in configs}
    if len(targets) != 1:
        raise ValueError("mixed target thresholds")
    sources, missing = sources_for(series, configs)
    if missing and not allow_partial:
        raise ValueError("incomplete series; pass --allow-partial for debugging only")
    if not sources:
        raise ValueError("no collected episodes")
    stats = None
    if final:
        stats = json.loads(frozen_preprocessing.read_text())
        if stats.get("fit_split") != "train" or set(stats["features"]) != set(
            FEATURE_NAMES
        ):
            raise ValueError("invalid frozen preprocessing")
        if (
            digest(frozen_preprocessing)
            != definition["frozen"]["preprocessing"]["sha256"]
        ):
            raise ValueError("preprocessing differs from frozen policy lock")
        if set(stats["fit_episode_ids"]).intersection(c["episode_id"] for c in configs):
            raise ValueError("preprocessing overlaps final test")
    manifest = begin(
        output, {"source_series": str(series.resolve()), "target_ms": targets.pop()}
    )
    manifest.update(
        status="failed",
        complete=False,
        missing_episodes=missing,
        frozen_policy_sha256=definition["frozen_policy_sha256"],
        sources=[
            {
                "episode_id": c["episode_id"],
                "split": c["split"],
                "profile": c["profile"],
                "path": str(p.resolve()),
                "manifest_sha256": digest(p / "manifest.json"),
            }
            for c, p in sources
        ],
    )
    try:
        quality, audits = export_rows(sources, output, definition["splits"])
        if stats is None:
            stats = fit(stream(output / "train.jsonl"))
        quality["missing_episodes"] = missing
        for name, value in (
            ("preprocessing", stats),
            ("quality", quality),
            ("feasibility", audits),
        ):
            (output / f"{name}.json").write_text(json.dumps(value, indent=2) + "\n")
        manifest.update(status="collected", complete=not missing)
    finally:
        finish(output, manifest)
    return quality
