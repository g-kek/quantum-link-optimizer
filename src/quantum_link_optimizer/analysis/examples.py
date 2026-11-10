"""Synthetic ping sets with a known cause, used to check the diagnosis.

These are illustrations of each mechanism, not a model of the operator
network. A correct answer here only shows the rules react as intended
"""

from datetime import UTC, datetime, timedelta

import numpy as np

from .probes import Probe

START = datetime(2026, 11, 1, tzinfo=UTC)
STEP_S = 10
SAMPLES = 24 * 3600 // STEP_S

EXPECTED = {
    "healthy": {"cause": "healthy"},
    "queueing": {"cause": "queueing"},
    "narrow-bandwidth": {"cause": "narrow_bandwidth"},
    "icmp-slow-path": {"cause": "icmp_slow_path"},
    "constant-delay": {"cause": "constant_delay", "segment": "gw → hub"},
    "route-change": {"cause": "route_change"},
    "flapping": {"cause": "flapping"},
    "shared-queue": {"cause": "queueing", "scope": "A+B"},
}

DESCRIPTIONS = {
    "healthy": "Оба пути 30–35 мс без перегрузки",
    "queueing": "Путь A: днём загрузка доходит до 98%, очередь до сотен мс",
    "narrow-bandwidth": "Путь A: участок около 33 кбит/с, ping 64/512/1400 байт",
    "icmp-slow-path": "Путь A: ICMP отвечает через процессор шифратора, UDP 30 мс",
    "constant-delay": "Путь A: туннель через удалённый узел добавляет 650 мс",
    "route-change": "Путь A: в 09:00 трафик уходит на резервный маршрут",
    "flapping": "Путь A: маршрут переключается между 40 и 700 мс",
    "shared-queue": "Пути A и B проходят общий перегруженный участок",
}


def daily_load(rng, peak=0.98):
    hours = np.arange(SAMPLES) * STEP_S / 3600
    day = np.clip(np.sin((hours - 6) / 24 * 2 * np.pi), 0, None)
    load = 0.3 + (peak - 0.3) * day + rng.normal(0, 0.01, SAMPLES)
    return np.clip(load, 0.05, 0.985)


def queue_ms(rng, load, service_ms=15.0):
    mean = service_ms * load / (1 - load)
    return np.minimum(rng.exponential(mean), 2000.0)


def jitter(rng, scale=1.0):
    return rng.exponential(scale, SAMPLES)


def series(name, path, rtt, **fields):
    rows = []

    for sample, value in enumerate(rtt):
        load = fields.get("load")
        rows.append(
            (
                Probe(
                    sample=sample,
                    time_s=(START + timedelta(seconds=sample * STEP_S)).timestamp(),
                    path=path,
                    status="ok",
                    rtt_ms=float(value),
                    source="synthetic_example",
                    packet_bytes=fields.get("packet_bytes"),
                    probe=fields.get("probe", "icmp"),
                    target=fields.get("target", "endpoint"),
                    load_percent=None if load is None else float(load[sample] * 100),
                ),
                name,
            )
        )

    return rows


def build(name, seed=7):
    rng = np.random.default_rng(seed)
    rows = []

    if name == "healthy":
        rows += series(name, "A", 28 + jitter(rng, 2))
        rows += series(name, "B", 34 + jitter(rng, 2))

    elif name == "queueing":
        load = daily_load(rng)
        rows += series(name, "A", 25 + queue_ms(rng, load), load=load)
        rows += series(name, "B", 34 + jitter(rng, 2))

    elif name == "narrow-bandwidth":
        for size in (64, 512, 1400):
            serialization = 2 * size * 8 / 33
            rows += series(
                name, "A", 25 + serialization + jitter(rng, 2), packet_bytes=size
            )

        rows += series(name, "B", 34 + jitter(rng, 2))

    elif name == "icmp-slow-path":
        rows += series(name, "A", 680 + rng.gamma(4, 15, SAMPLES), probe="icmp")
        rows += series(name, "A", 30 + jitter(rng, 2), probe="udp")
        rows += series(name, "B", 34 + jitter(rng, 2))

    elif name == "constant-delay":
        rows += series(name, "A", 3 + jitter(rng, 0.5), target="gw")
        rows += series(name, "A", 655 + jitter(rng, 3), target="hub")
        rows += series(name, "A", 690 + jitter(rng, 3), target="endpoint")
        rows += series(name, "B", 34 + jitter(rng, 2))

    elif name == "route-change":
        base = np.where(np.arange(SAMPLES) < 9 * 3600 // STEP_S, 30.0, 690.0)
        rows += series(name, "A", base + jitter(rng, 3))
        rows += series(name, "B", 34 + jitter(rng, 2))

    elif name == "flapping":
        state, level = 0, np.empty(SAMPLES)

        while state < SAMPLES:
            length = int(rng.integers(30, 90))
            level[state : state + length] = rng.choice([40.0, 700.0])
            state += length

        rows += series(name, "A", level + jitter(rng, 3))
        rows += series(name, "B", 34 + jitter(rng, 2))

    elif name == "shared-queue":
        load = daily_load(rng)
        common = queue_ms(rng, load)
        rows += series(name, "A", 25 + common + jitter(rng, 2), load=load)
        rows += series(name, "B", 35 + common + jitter(rng, 2), load=load)

    else:
        raise ValueError(f"unknown example {name}")

    return rows
