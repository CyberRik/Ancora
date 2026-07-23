"""Scheduler entrypoint: ``ancora-scheduler``."""

from __future__ import annotations

import logging

import uvicorn

from ancora_common.logging import configure_logging
from ancora_scheduler.api import create_app
from ancora_scheduler.settings import get_settings

logger = logging.getLogger("ancora.scheduler")

app = create_app()


def main() -> None:
    settings = get_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    logger.info(
        "scheduler starting on %s:%s (config=%s)",
        settings.host,
        settings.port,
        settings.scheduler_config_path or "<defaults>",
    )
    uvicorn.run(app, host=settings.host, port=settings.port, log_config=None)


if __name__ == "__main__":  # pragma: no cover
    main()
