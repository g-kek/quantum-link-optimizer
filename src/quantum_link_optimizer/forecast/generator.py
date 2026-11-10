"""Synthetic two-path week with causes that can and cannot be foreseen.

Load follows working hours and drives a queue, a path slowly degrades,
the key pool drains when demand outgrows generation, and sudden route
steps arrive at random. Every sample keeps the episode that shaped it,
so results can be split into foreseeable and sudden cases. Numbers are
illustrations, not measurements of the operator network
"""

from dataclasses import dataclass

import numpy as np

STEP_S = 10
DAY = 24 * 3600 // STEP_S


@dataclass
class Week:
    time_s: np.ndarray
    rtt_ms: np.ndarray
    load: np.ndarray
    loss: np.ndarray
    keys: np.ndarray
    episode: np.ndarray
    step_s: int = STEP_S


def working_hours(samples, rng):
    hours = np.arange(samples) * STEP_S / 3600
    day = (hours // 24).astype(int) % 7
    shape = np.clip(np.sin((hours % 24 - 7) / 12 * np.pi), 0, None) ** 1.5
    weekday = np.where(day < 5, 1.0, 0.8)
    return 0.25 + 0.66 * shape * weekday + rng.normal(0, 0.015, samples)


def bursts(load, rng, count):
    """Load ramps up over 15 minutes before a burst, that is the precursor."""
    label = np.zeros(len(load), dtype=bool)

    for start in rng.integers(DAY // 4, len(load) - DAY // 8, count):
        ramp = 90
        hold = int(rng.integers(60, 180))
        load[start : start + ramp] += np.linspace(0, 0.3, ramp)
        load[start + ramp : start + ramp + hold] += 0.3
        load[start + ramp + hold : start + 2 * ramp + hold] += np.linspace(0.3, 0, ramp)
        label[start + ramp // 2 : start + 2 * ramp + hold] = True

    return label


def generate(days=7, seed=42):
    rng = np.random.default_rng(seed)
    samples = days * DAY
    rtt = np.empty((samples, 2))
    load = np.empty((samples, 2))
    loss = np.zeros((samples, 2))
    episode = np.full((samples, 2), "", dtype=object)
    base = np.array([30.0, 38.0])

    for path in range(2):
        level = working_hours(samples, rng) * (0.92 if path else 1.0)
        burst = bursts(level, rng, count=2 * days)
        level = np.clip(level, 0.05, 0.97)
        queue = rng.exponential(12 * level / (1 - level))
        drift = np.zeros(samples)

        # Gradual degradation: loss creeps up first, then the delay follows
        for start in rng.integers(DAY, samples - DAY // 2, max(1, days // 3)):
            length = int(rng.integers(360, 720))
            ramp = np.linspace(0, 1, length) ** 2
            loss[start : start + length, path] += 0.04 * np.linspace(0, 1, length)
            drift[start + length // 3 : start + length] += (
                140 * ramp[: length - length // 3]
            )
            episode[start : start + length, path] = "degradation"

        # Sudden steps: a new route, nothing hints at them in advance
        for start in rng.integers(DAY // 2, samples - DAY // 4, days):
            length = int(rng.integers(30, 120))
            drift[start : start + length] += rng.uniform(80, 200)
            episode[start : start + length, path] = "sudden"

        busy = burst | (level >= 0.8)
        episode[busy & (episode[:, path] == ""), path] = "load"
        rtt[:, path] = base[path] + queue + drift + rng.normal(0, 1.5, samples)
        load[:, path] = level

    demand = 0.6 * load.mean(axis=1) + 0.1
    generation = np.full(samples, 0.45)

    # A few hours of reduced key generation, the pool drains predictably
    for start in rng.integers(DAY, samples - DAY // 2, max(1, days // 3)):
        generation[start : start + int(rng.integers(360, 900))] = 0.15

    pool = np.empty(samples)
    level = 400.0

    for index in range(samples):
        level = float(
            np.clip(level + (generation[index] - demand[index]) * STEP_S, 0, 400)
        )
        pool[index] = level

    starved = pool < 1
    rtt[starved] += rng.uniform(60, 160, (starved.sum(), 2))
    drained = (pool < 150) & (generation < demand)
    episode[drained & (episode[:, 0] == ""), 0] = "keys"
    episode[drained & (episode[:, 1] == ""), 1] = "keys"
    return Week(
        time_s=np.arange(samples) * STEP_S + 1_793_577_600.0,
        rtt_ms=np.maximum(rtt, 1.0),
        load=np.clip(load + rng.normal(0, 0.01, load.shape), 0, 1),
        loss=np.clip(loss + rng.normal(0, 0.002, loss.shape), 0, 1),
        keys=pool,
        episode=episode,
    )
