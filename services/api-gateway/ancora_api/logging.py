"""Structured JSON logging.

Every log line carries a stable shape so downstream (Phase 4) can correlate by
``run_id``/``trace_id``. In dev you can flip ``ANCORA_LOG_JSON=false`` for plain text.
"""

from __future__ import annotations

import logging
import sys

from pythonjsonlogger import json as jsonlogger


def configure_logging(*, level: str = "INFO", json_output: bool = True) -> None:
    """Configure root logging once at process start."""
    root = logging.getLogger()
    root.setLevel(level.upper())

    # Reset handlers so re-invocation (tests, reload) doesn't duplicate output.
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
