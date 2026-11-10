import time

from quantum_link_optimizer.selection.config import Settings
from quantum_link_optimizer.selection.race import hedge_plan
from quantum_link_optimizer.selection.state import Observations


class Sessions:
    class session:
        @staticmethod
        def phase():
            return "steady"


def probes(observations, values, path="A"):
    for sample, value in enumerate(values):
        observations.observe(
            "probe_result", path=path, sample=sample, status="ok", rtt_ms=value
        )


def ewma_after_swap(restart):
    observations = Observations(Settings(ewma_restart=restart))
    probes(observations, [160.0] * 12 + [40.0])
    snapshot = observations.snapshot(
        time.monotonic_ns(), {"A": Sessions, "B": Sessions}
    )
    return snapshot["A"]["ewma_rtt_ms"]


def test_ewma_restarts_at_a_detected_step():
    assert ewma_after_swap(restart=True) == 40.0
    assert ewma_after_swap(restart=False) > 120.0


class Router:
    def __init__(self, settings, values):
        self.settings = settings
        self.observations = Observations(settings)
        probes(self.observations, values)


def test_copy_waits_for_the_usual_primary_rtt():
    settings = Settings(hedge_delay_ms=40, adaptive_hedge=True)
    router = Router(settings, [60.0] * 9 + [70.0])
    delay, risk = hedge_plan(router, "A", time.monotonic_ns())

    assert 60.0 <= delay <= 70.0
    assert risk == 0.0


def test_slow_primary_raises_the_risk():
    settings = Settings(hedge_delay_ms=40, adaptive_hedge=True, hedge_risk=0.2)
    router = Router(settings, [60.0, 150.0, 160.0, 60.0, 170.0, 60.0])
    _, risk = hedge_plan(router, "A", time.monotonic_ns())

    assert risk == 0.5


def test_fixed_delay_without_adaptive_hedge():
    router = Router(Settings(hedge_delay_ms=40), [60.0] * 10)

    assert hedge_plan(router, "A", time.monotonic_ns()) == (40, None)
