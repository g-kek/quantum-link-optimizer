import pytest

from quantum_link_optimizer.lab.config import validate_link


def test_fair_queue_must_not_replace_netem_delay_on_same_interface():
    validate_link({"queue_discipline": "fq_codel", "rate_mbit": 1, "limit": 100})
    with pytest.raises(ValueError, match="separate interface"):
        validate_link({"queue_discipline": "fq_codel", "rate_mbit": 1, "delay_ms": 30})
    with pytest.raises(ValueError, match="unknown queue"):
        validate_link({"queue_discipline": "unknown"})
