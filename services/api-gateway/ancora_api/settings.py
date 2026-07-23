"""API Gateway configuration.

Inherits the shared DB/Temporal fields from ``CommonSettings`` and adds the
gateway-specific ones (logging, CORS).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field

from ancora_common.settings import CommonSettings


class Settings(CommonSettings):
    environment: str = Field(default="development")
    log_level: str = Field(default="INFO")
    log_json: bool = Field(default=True)

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    # Chaos injection (kill a worker from the UI). Off unless explicitly enabled:
    # it requires the Docker socket, which lets this process control its host's
    # containers. The local compose stack turns it on; nothing else should.
    chaos_enabled: bool = Field(default=False)
    docker_socket: str = Field(default="/var/run/docker.sock")
    # Compose project name, so injections can never reach another stack's containers.
    compose_project: str = Field(default="ancora")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached Settings instance."""
    return Settings()
