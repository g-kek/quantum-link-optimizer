"""Likely causes of high RTT with the measurements behind each of them."""

CAUSES = {
    "queueing": "Очередь",
    "narrow_bandwidth": "Узкая полоса",
    "icmp_slow_path": "Медленная обработка ICMP",
    "constant_delay": "Постоянная задержка",
    "route_change": "Смена маршрута",
    "flapping": "Мигание маршрута",
    "loss": "Потери и повторы",
    "healthy": "Превышений цели нет",
    "not_established": "Причина не установлена",
}

NEXT_TESTS = {
    "queueing": "Ping в простое и под нагрузкой, счётчики очередей, drops и загрузки",
    "narrow_bandwidth": "Узнать полосу каждого участка, ping 64/512/1400 байт",
    "icmp_slow_path": "Сравнить с UDP/TCP/STAMP до той же точки",
    "constant_delay": "Traceroute/MTR и ping до промежуточных узлов, схема туннелей",
    "route_change": "Журнал изменений маршрутов и аварий за время ступени",
    "flapping": "Traceroute в обоих состояниях, проверить ECMP и резервирование",
    "loss": "Счётчики errors/drops, ping с меньшим интервалом",
}


def clip(value):
    return max(0.0, min(1.0, value))


def measured(metric, value, unit):
    return {"metric": metric, "value": value, "unit": unit}


def worst_rtt(summary):
    values = [summary.get("p95_ms") or 0.0]
    sweep, kinds = summary.get("size_sweep"), summary.get("probe_types")

    if sweep:
        values.append(max(sweep["floor_ms_by_size"].values()))

    if kinds:
        values.extend(kinds["median_ms"].values())

    return max(values)


def path_hypotheses(summary, target_ms):
    if "median_ms" not in summary:
        return []

    found = []
    median = summary["median_ms"]
    shape = summary["shape"]
    modes = shape["modes"]
    flapping = modes is not None and modes["tight"] and modes["switches_per_hour"] >= 2

    def add(cause, score, evidence):
        if score > 0:
            found.append(
                {
                    "cause": cause,
                    "title": CAUSES[cause],
                    "scope": summary["path"],
                    "score": round(clip(score), 3),
                    "evidence": evidence,
                    "next_test": NEXT_TESTS[cause],
                }
            )

    share = summary["queue_share"]
    score = (share - 0.15) / 0.45
    evidence = [
        measured("base_ms", summary["base_ms"], "ms"),
        measured("queue_median_ms", summary["queue_median_ms"], "ms"),
        measured("queue_share", share, "fraction"),
    ]
    load = summary.get("load")

    if load and load["spearman_load_queue"] is not None:
        evidence.append(
            measured("spearman_load_queue", load["spearman_load_queue"], "")
        )

        if load["spearman_load_queue"] >= 0.5:
            score = max(score, 0.6) + 0.2

    daily = shape["daily"]

    if daily and daily["peak_to_quiet_ratio"] and daily["peak_to_quiet_ratio"] >= 1.5:
        evidence.append(measured("peak_hour_utc", daily["peak_hour_utc"], "hour"))
        score += 0.1

    if flapping:
        score *= 0.5

    add("queueing", score, evidence)
    sweep = summary.get("size_sweep")

    if sweep and sweep["significant"]:
        largest = max(sweep["floor_ms_by_size"].values())
        add(
            "narrow_bandwidth",
            1.5 * sweep["serialization_ms_largest"] / largest,
            [
                measured("bottleneck_kbit_s", sweep["bottleneck_kbit_s"], "kbit/s"),
                measured(
                    "serialization_ms_largest", sweep["serialization_ms_largest"], "ms"
                ),
                measured("largest_bytes", sweep["sizes_bytes"][-1], "bytes"),
            ],
        )

    kinds = summary.get("probe_types")

    if kinds:
        excess = kinds["icmp_excess_ms"]
        floor = kinds["floor_ms"]["icmp"]

        if excess > max(5.0, 0.2 * (floor - excess)):
            add(
                "icmp_slow_path",
                1.5 * excess / floor,
                [
                    measured("icmp_excess_ms", excess, "ms"),
                    *(
                        measured(f"{name}_floor_ms", value, "ms")
                        for name, value in kinds["floor_ms"].items()
                    ),
                ],
            )

    budget = {part["id"]: part["ms"] for part in summary.get("budget") or []}
    unexplained = budget.get("other_constant", summary["base_ms"])
    steps = shape["steps"]

    if summary["base_ms"] > 0.5 * target_ms and not flapping:
        # A step in the base explains the constant part better than distance
        damping = 0.6 if steps else 1.0
        add(
            "constant_delay",
            1.2 * damping * unexplained / median,
            [
                measured("base_ms", summary["base_ms"], "ms"),
                measured("unexplained_constant_ms", unexplained, "ms"),
                measured("jitter_ms", summary["jitter_ms"], "ms"),
            ],
        )

    if steps and not flapping:
        largest = max(abs(step["after_ms"] - step["before_ms"]) for step in steps)
        add(
            "route_change",
            0.6 + 0.3 * clip(largest / median),
            [measured("level_steps", steps, "list")],
        )

    if flapping:
        add(
            "flapping",
            0.6 + 0.3 * modes["separation"],
            [
                measured("low_mode_ms", modes["low_ms"], "ms"),
                measured("high_mode_ms", modes["high_ms"], "ms"),
                measured("switches_per_hour", modes["switches_per_hour"], "1/h"),
            ],
        )

    if summary["loss_rate"] and summary["loss_rate"] >= 0.01:
        add(
            "loss",
            10 * summary["loss_rate"],
            [measured("loss_rate", summary["loss_rate"], "fraction")],
        )

    return sorted(found, key=lambda item: item["score"], reverse=True)


def status(score):
    if score >= 0.6:
        return "likely"

    if score >= 0.3:
        return "possible"

    return "not_established"


def where(cause, path, segments):
    route = segments.get(path)

    if not route:
        return None

    field = "added_variable_ms" if cause == "queueing" else "added_floor_ms"
    worst = max(route["segments"], key=lambda row: row[field])
    return {
        "segment": f"{worst['from']} → {worst['to']}",
        "added_ms": worst[field],
        "basis": "Разность RTT до соседних точек измерения",
    }


def missing_tests(decomposition, segments):
    missing = []
    paths = decomposition["paths"].values()

    if not any(p.get("size_sweep") for p in paths):
        missing.append("Ping разного размера: 64, 512 и 1400 байт")

    if not any(p.get("probe_types") for p in paths):
        missing.append("UDP/TCP или STAMP рядом с ICMP до той же точки")

    if not any(p.get("load") for p in paths):
        missing.append("Загрузка каналов за то же время")

    if not segments:
        missing.append("Пробы до промежуточных узлов каждого пути")

    if not any(p.get("shape", {}).get("daily") for p in paths):
        missing.append("Не меньше суток измерений с шагом до 10 с")

    return missing


def diagnose(decomposition, segments, target_ms):
    per_path = {}
    verdicts = {}

    for path, summary in decomposition["paths"].items():
        hypotheses = path_hypotheses(summary, target_ms)
        per_path[path] = hypotheses

        if "median_ms" not in summary:
            verdicts[path] = {
                "cause": "not_established",
                "title": CAUSES["not_established"],
            }
            continue

        if worst_rtt(summary) <= target_ms and summary["above_target_share"] < 0.05:
            verdicts[path] = {
                "cause": "healthy",
                "title": CAUSES["healthy"],
                "score": 1.0,
            }
            continue

        top = hypotheses[0] if hypotheses else None

        if top is None or top["score"] < 0.3:
            verdicts[path] = {
                "cause": "not_established",
                "title": CAUSES["not_established"],
            }
            continue

        verdicts[path] = {
            **top,
            "status": status(top["score"]),
            "where": where(top["cause"], path, segments),
        }

    shared = decomposition.get("shared")
    same = len(verdicts) == 2 and len({v["cause"] for v in verdicts.values()}) == 1

    if (
        shared
        and shared["spearman_queue"] is not None
        and shared["spearman_queue"] >= 0.6
    ):
        if same and verdicts["A"]["cause"] == "queueing":
            for verdict in verdicts.values():
                verdict["scope"] = "A+B"
                verdict["where"] = {
                    "segment": "Общий участок A и B",
                    "added_ms": None,
                    "basis": "Очереди A и B растут одновременно, "
                    f"Spearman {shared['spearman_queue']:.2f}",
                }

    order = sorted(
        decomposition["paths"],
        key=lambda path: worst_rtt(decomposition["paths"][path]),
        reverse=True,
    )
    return {
        "verdict": verdicts[order[0]] if order else None,
        "by_path": verdicts,
        "hypotheses": per_path,
        "missing_tests": missing_tests(decomposition, segments),
        "scope": (
            "Гипотезы по измерениям RTT, а не подтверждённая причина, "
            "подтверждение требует данных оборудования и журналов"
        ),
    }
