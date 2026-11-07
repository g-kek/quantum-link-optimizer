from quantum_link_optimizer.selection.policy import Policy

from .features import vector


class ForecastPolicy(Policy):
    def __init__(self, settings, model, penalty_ms=100):
        super().__init__(settings)
        self.model = model
        self.penalty_ms = penalty_ms

    def choose(self, snapshot, now_ns):
        probabilities = self.model.predict([vector(snapshot[p]) for p in ("A", "B")])

        for path, values in zip(("A", "B"), probabilities, strict=True):
            snapshot[path]["predicted_late_probability"] = values[0]
            snapshot[path]["predicted_empty_pool_probability"] = values[1]

        return super().choose(snapshot, now_ns)

    def score(self, row):
        return super().score(row) + self.penalty_ms * row.get(
            "predicted_late_probability", 0
        )
