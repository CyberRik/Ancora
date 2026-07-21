"""The :class:`Workflow` base class.

Workflow code is **deterministic**: it may only orchestrate. All I/O and
non-determinism happen inside activities (Phase 2 dispatches those to Ray). The
helpers here are thin, replay-safe wrappers over Temporal primitives.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from datetime import timedelta
from typing import Any, TypeVar

from temporalio import workflow
from temporalio.common import RetryPolicy

_T = TypeVar("_T")

# A sane default so a hung activity cannot pin a workflow forever. Individual
# calls override per node class once the scheduler lands (Phase 3).
_DEFAULT_START_TO_CLOSE = timedelta(seconds=60)


class Workflow:
    """Base class for Ancora workflows.

    Subclass it, decorate the class with ``@workflow.defn`` and the entrypoint
    with ``@workflow.run``. Inside ``run`` use :meth:`call` to execute activities
    durably and :meth:`gather` to fan out in parallel.
    """

    async def call(
        self,
        activity: Callable[..., Awaitable[_T]] | str,
        arg: Any = None,
        *,
        start_to_close_timeout: timedelta = _DEFAULT_START_TO_CLOSE,
        schedule_to_close_timeout: timedelta | None = None,
        retry: RetryPolicy | None = None,
        task_queue: str | None = None,
        heartbeat_timeout: timedelta | None = None,
    ) -> _T:
        """Durably execute a single activity and return its result.

        Every completed call is an implicit checkpoint in Temporal history — if a
        worker dies, replay reconstructs everything up to here without re-running it.
        """
        kwargs: dict[str, Any] = {
            "start_to_close_timeout": start_to_close_timeout,
        }
        if schedule_to_close_timeout is not None:
            kwargs["schedule_to_close_timeout"] = schedule_to_close_timeout
        if retry is not None:
            kwargs["retry_policy"] = retry
        if task_queue is not None:
            kwargs["task_queue"] = task_queue
        if heartbeat_timeout is not None:
            kwargs["heartbeat_timeout"] = heartbeat_timeout

        if arg is None:
            return await workflow.execute_activity(activity, **kwargs)
        return await workflow.execute_activity(activity, arg, **kwargs)

    async def gather(self, *awaitables: Awaitable[_T]) -> Sequence[_T]:
        """Await several :meth:`call` coroutines in parallel (deterministic order)."""
        return await asyncio.gather(*awaitables)
