"""Shared configuration for server-side components.

Each service may subclass ``CommonSettings`` to add its own fields; the shared
fields (DB, Temporal) keep the same env names everywhere so one ``.env`` works
across the API gateway and the workers.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class CommonSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ANCORA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+asyncpg://ancora:ancora@localhost:5432/ancora"

    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"

    # Phase 1 uses a single task queue. Phase 2 introduces per-capability queues
    # (gpu/cpu/io); this stays the default/orchestration queue.
    task_queue: str = "ancora-default"

    # Redis backs worker-liveness TTLs and (later) rate-limit token buckets.
    redis_url: str = "redis://localhost:6379/0"

    # Ray cluster address. "auto"/"local" (or empty) start/attach a local Ray;
    # "ray://host:10001" attaches to a running head. Empty falls back to the
    # in-process LocalBackend so activities run without a cluster.
    ray_address: str = ""
