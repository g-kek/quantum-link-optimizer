import time
from types import SimpleNamespace

from quantum_link_optimizer.experiments.forecasting.policy import ForecastPolicy
from quantum_link_optimizer.selection.config import Settings
from quantum_link_optimizer.selection.state import Observations


class CertainForecast:
    def predict(self, _):
        return [[0.0, 0.0], [1.0, 1.0]]


def test_forecast_never_overrides_known_path_ineligibility():
    settings = Settings(min_hold_ms=0)
    observations = Observations(settings)

    for path in ("A", "B"):
        observations.observe("probe_result", path=path, status="ok", rtt_ms=60)

    senders = {
        path: SimpleNamespace(session=SimpleNamespace(phase=lambda: "steady"))
        for path in ("A", "B")
    }
    now = time.monotonic_ns()
    snapshot = observations.snapshot(now, senders)
    snapshot["A"]["device_available"] = False
    policy = ForecastPolicy(settings, CertainForecast())
    chosen, reason = policy.choose(snapshot, now)

    assert chosen == "B"
    assert reason == "emergency"
    assert snapshot["B"]["predicted_late_probability"] == 1
