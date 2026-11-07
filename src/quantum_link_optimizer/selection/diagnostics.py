"""Compatibility entry point for stateless A/B diagnostic observations."""

from quantum_link_optimizer.diagnostics.rules import assess


def diagnose(
    snapshot, shared_segment=False, local_queue_depth=0, target_ms=100, max_age_ms=2000
):
    return assess(snapshot, shared_segment, local_queue_depth, target_ms, max_age_ms)
