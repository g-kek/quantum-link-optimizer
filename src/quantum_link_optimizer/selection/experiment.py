import json
import re
from copy import deepcopy

from quantum_link_optimizer.collection.collect import collect_episode
from quantum_link_optimizer.collection.plan import episodes
from quantum_link_optimizer.selection.config import Settings
from quantum_link_optimizer.telemetry_lab.manifest import begin, digest, finish


async def compare(plan, output):
    configs = [
        config
        for config in episodes(plan["dataset"])
        if config["split"] in {"train", "validation"} and config["fixed_path"] == "A"
    ]
    names = set()
    if not configs or not plan["policies"]:
        raise ValueError("comparison needs episodes and policies")
    for policy in plan["policies"]:
        name = policy["name"]
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", name) or name in names:
            raise ValueError("policy names must be unique safe identifiers")
        names.add(name)
        settings = Settings(**policy["settings"])
        if any(settings.target_ms != config["target_ms"] for config in configs):
            raise ValueError("routing and evaluation targets must match")
        if type(policy.get("allow_copy", False)) is not bool:
            raise ValueError("copy authorization must be boolean")
    manifest = begin(output, plan)
    manifest["status"] = "failed"
    summaries = []
    try:
        for config in configs:
            for policy in plan["policies"]:
                current = deepcopy(config)
                current["routing"] = policy["settings"]
                current["allow_copy"] = policy.get("allow_copy", False)
                current["warmup_samples"] = plan.get("warmup_samples", 0)
                current["episode_id"] += "-" + policy["name"]
                path = output / current["episode_id"]
                await collect_episode(current, path)
                summaries.append(
                    {
                        "profile": current["profile"],
                        "policy": policy["name"],
                        "seed": current["seed"],
                        "split": current["split"],
                        "path": path.name,
                        "manifest_sha256": digest(path / "manifest.json"),
                        **json.loads((path / "routing-summary.json").read_text()),
                    }
                )
        (output / "comparison.json").write_text(json.dumps(summaries, indent=2) + "\n")
        table = [
            "# Сравнение онлайн-политик",
            "",
            "Лабораторные UDP-команды; final test не используется.",
            "",
            "| Сценарий | Политика | Вовремя / поступило | Исполнений | Копии, байт |",
            "| --- | --- | --- | --- | --- |",
        ]
        table += [
            (
                f"| {row['profile']} | {row['policy']} | "
                f"{row['timely_executed']} / {row['offered']} | "
                f"{row['executions']} | {row['extra_path_bytes']} |"
            )
            for row in summaries
        ]
        (output / "comparison.md").write_text("\n".join(table) + "\n")
        manifest["status"] = "collected"
    finally:
        finish(output, manifest)
    return summaries
