"""Scheduler service settings (env-driven, ``ANCORA_`` prefixed)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class SchedulerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ANCORA_", extra="ignore")

    environment: str = "development"
    log_level: str = "INFO"
    log_json: bool = False
    host: str = "0.0.0.0"
    port: int = 8090
    # Path to the declarative policy document (AN-048). Absent = built-in defaults.
    scheduler_config_path: Path | None = None
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])


@lru_cache
def get_settings() -> SchedulerSettings:
    return SchedulerSettings()
