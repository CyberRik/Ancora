"""API Gateway configuration.

Inherits the shared DB/Temporal fields from ``CommonSettings`` and adds the
gateway-specific ones (logging, CORS).
"""

from __future__ import annotations

from functools import lru_cache

from ancora_common.settings import CommonSettings
from pydantic import Field


class Settings(CommonSettings):
    environment: str = Field(default="development")
    log_level: str = Field(default="INFO")
    log_json: bool = Field(default=True)

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
