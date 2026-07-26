"""API-gateway metrics (Phase 4c).

A process-global registry (like the chaos log in ``deps``) so any router can
record without threading state through the app. Kept intentionally small — the
control plane's interesting signals are "how many runs are being started" and
"how many live streams are open right now".
"""

from __future__ import annotations

from ancora_common.metrics import MetricsRegistry

REGISTRY = MetricsRegistry()

RUNS_STARTED = REGISTRY.counter(
    "ancora_api_runs_started_total", "Workflow runs started via the API, by workflow."
)
STREAM_CONNECTIONS = REGISTRY.counter(
    "ancora_api_run_stream_connections_total", "Run-stream WebSocket connections opened."
)
STREAM_ACTIVE = REGISTRY.gauge(
    "ancora_api_run_stream_active", "Run-stream WebSocket connections currently open."
)
