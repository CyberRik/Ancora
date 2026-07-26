"""Event-consumer metrics (Phase 4c).

The consumer is the busiest projection path, so its metrics answer "is the event
stream flowing, and are runs settling?" — the two things that break silently.
"""

from __future__ import annotations

from ancora_common.metrics import MetricsRegistry

REGISTRY = MetricsRegistry()

EVENTS_PROJECTED = REGISTRY.counter(
    "ancora_consumer_events_projected_total", "Events processed from the event stream, by kind."
)
PROJECTOR_BATCHES = REGISTRY.counter(
    "ancora_consumer_projector_batches_total", "Event batches claimed from the stream."
)
RUNS_SETTLED = REGISTRY.counter(
    "ancora_consumer_runs_settled_total", "Runs the reconciler transitioned to a terminal state."
)
RECONCILE_PASSES = REGISTRY.counter(
    "ancora_consumer_reconcile_passes_total", "Reconciler passes completed."
)
