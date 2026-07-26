"""Event-consumer configuration."""

from __future__ import annotations

import socket

from ancora_common.settings import CommonSettings


class ConsumerSettings(CommonSettings):
    log_level: str = "INFO"
    log_json: bool = True

    # Stable name for this consumer within the projector group. Defaults to the
    # hostname so multiple replicas share the group's work without stepping on
    # each other (each claims distinct pending entries).
    consumer_name: str = f"consumer-{socket.gethostname()}"

    # How many events to claim per read, and how long to block waiting for them.
    batch_size: int = 128
    block_ms: int = 5000

    # Reconciler cadence: how often to re-derive run status from Temporal for
    # non-terminal runs (the authoritative heal for run-level lifecycle, which the
    # activity interceptor never sees). Kept short so a completed run settles fast.
    reconcile_interval_seconds: float = 8.0

    # Toggle either half off independently (tests, or running projector-only).
    run_projector: bool = True
    run_reconciler: bool = True
