"""Runtime configuration, sourced from environment variables.

All settings are 12-factor: defaults are dev-friendly, production overrides come
from the environment. Prefix every var with ``ANCORA_``.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ANCORA_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = Field(default="development")
    log_level: str = Field(default="INFO")
    log_json: bool = Field(default=True)

    # Postgres async DSN (SQLAlchemy + asyncpg).
    database_url: str = Field(default="postgresql+asyncpg://ancora:ancora@localhost:5432/ancora")

    # CORS origins for the dashboard.
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
