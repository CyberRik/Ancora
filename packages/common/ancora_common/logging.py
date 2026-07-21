"""Structured JSON logging shared across services.

Every service configures this at startup so log lines share one shape and can be
correlated by ``run_id``/``trace_id`` once those land (Phase 4).
"""

from __future__ import annotations

import logging
import sys

from pythonjsonlogger import json as jsonlogger


def configure_logging(*, level: str = "INFO", json_output: bool = True) -> None:
    """Configure root logging once at process start (idempotent)."""
    root = logging.getLogger()
    root.setLevel(level.upper())

    for existing in list(root.handlers):
        root.removeHandler(existing)

    handler = logging.StreamHandler(stream=sys.stdout)
    if json_output:
        handler.setFormatter(
            jsonlogger.JsonFormatter(
                "%(asctime)s %(levelname)s %(name)s %(message)s",
                rename_fields={"asctime": "ts", "levelname": "level", "name": "logger"},
            )
        )
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s"))
    root.addHandler(handler)
