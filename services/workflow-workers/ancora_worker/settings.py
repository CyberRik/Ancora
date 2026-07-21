"""Worker configuration."""

from __future__ import annotations

from ancora_common.settings import CommonSettings


class WorkerSettings(CommonSettings):
    log_level: str = "INFO"
    log_json: bool = True

    # Whether to report the registered workflow catalog to the DB on startup.
    # Disabled in tests (no database).
    report_catalog: bool = True

    # Max concurrent activity / workflow tasks (kept modest for Phase 1).
    max_concurrent_activities: int = 20
