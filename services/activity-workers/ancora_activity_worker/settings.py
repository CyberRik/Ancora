"""Activity worker configuration."""

from __future__ import annotations

import socket

from pydantic import Field

from ancora_common.resources import Capability
from ancora_common.settings import CommonSettings


class ActivityWorkerSettings(CommonSettings):
    log_level: str = "INFO"
    log_json: bool = True

    # Capability pools this worker serves. It polls exactly the queues these map
    # to (AN-033), so a cpu-only worker never receives gpu work.
    pools: list[Capability] = Field(default_factory=lambda: [Capability.CPU])

    # Advertised resources (fed to Ray accounting / the registry).
    total_cpus: float = 4.0
    total_gpus: float = 0.0
    accelerator_type: str | None = None

    # Registry + liveness.
    worker_id: str = Field(default_factory=lambda: f"aw-{socket.gethostname()}")
    register: bool = True
    heartbeat_interval_seconds: float = 5.0
    # Redis liveness TTL should comfortably exceed the heartbeat interval.
    liveness_ttl_seconds: int = 20

    max_concurrent_activities: int = 50
