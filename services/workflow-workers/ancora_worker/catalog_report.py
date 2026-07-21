"""Report the worker's registered workflows into the catalog on startup.

Registration is idempotent and code-addressed: a change to a workflow's source
bumps its version (see ``ancora_common.catalog.register_workflow``). This is how
the API can start a run by workflow name without importing workflow code.
"""

from __future__ import annotations

import hashlib
import inspect
import logging

from ancora_common import DEFAULT_PROJECT_ID
from ancora_common.catalog import register_workflow
from ancora_common.db import session_scope

from ancora import __version__ as sdk_version
from ancora_worker.examples import WORKFLOW_NAMES

logger = logging.getLogger("ancora.worker.catalog")


def _code_hash(cls: type) -> str:
    try:
        source = inspect.getsource(cls)
    except OSError:
        source = cls.__qualname__
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


async def report_catalog(task_queue: str) -> None:
    """Upsert each registered workflow's definition/version into the DB."""
    determinism_token = f"phase1-sdk-{sdk_version}"
    async with session_scope() as session:
        for cls, name in WORKFLOW_NAMES.items():
            version = await register_workflow(
                session,
                project_id=DEFAULT_PROJECT_ID,
                name=name,
                dag_spec={"style": "imperative", "workflow": name},
                code_hash=_code_hash(cls),
                determinism_token=determinism_token,
                task_queue=task_queue,
            )
            logger.info("registered workflow", extra={"workflow": name, "version": version.version})
