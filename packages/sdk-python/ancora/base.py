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

from pydantic import BaseModel
from temporalio import workflow
from temporalio.common import RetryPolicy

from ancora.nodes.idempotency import derive_idempotency_key

_T = TypeVar("_T")

# A sane default so a hung activity cannot pin a workflow forever. Individual
# calls override per node class once the scheduler lands (Phase 3).
_DEFAULT_START_TO_CLOSE = timedelta(seconds=60)


class ApprovalDecision(BaseModel):
    """A human decision delivered to a durable approval gate (AN-055)."""

    gate_id: str
    approved: bool
    comment: str = ""
    decided_by: str | None = None
    timed_out: bool = False


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

    async def call_node(
        self,
        type_name: str,
        node_id: str,
        node_input: BaseModel | dict[str, Any],
        *,
        task_queue: str | None = None,
        idempotency_key: str | None = None,
        start_to_close_timeout: timedelta = _DEFAULT_START_TO_CLOSE,
        retry: RetryPolicy | None = None,
    ) -> dict[str, Any]:
        """Execute a built-in node as an activity, with a derived idempotency key.

        The key is derived deterministically from ``(workflow_id, node_id, input)``
        (AN-062), so retries and replays of the same logical call reuse the same
        inbox entry and the node's side effect happens exactly once (AN-061).
        Returns ``{"output": <node output>, "cost": <cost>}``.
        """
        payload = (
            node_input.model_dump(mode="json") if isinstance(node_input, BaseModel) else node_input
        )
        key = derive_idempotency_key(
            workflow_id=workflow.info().workflow_id,
            node_id=node_id,
            payload=payload,
            override=idempotency_key,
        )
        req: dict[str, Any] = {
            "type_name": type_name,
            "node_id": node_id,
            "input": payload,
            "idempotency_key": key,
            "workflow_id": workflow.info().workflow_id,
        }
        result: dict[str, Any] = await self.call(
            "run_node",
            req,
            task_queue=task_queue,
            start_to_close_timeout=start_to_close_timeout,
            retry=retry,
        )
        return result

    # ---- durable human-in-the-loop ------------------------------------- #
    def _decisions(self) -> dict[str, ApprovalDecision]:
        """Lazily-initialized decision store, so subclasses need no ``super().__init__``."""
        store = self.__dict__.get("_ancora_decisions")
        if store is None:
            store = {}
            self.__dict__["_ancora_decisions"] = store
        return store

    async def approval(self, gate_id: str, *, timeout: timedelta | None = None) -> ApprovalDecision:
        """Durably wait for a decision on ``gate_id`` (AN-055).

        Consumes zero compute while parked and survives worker restarts — the wait
        is a Temporal condition, resumed by the ``submit_decision`` signal. If
        ``timeout`` elapses first, returns a synthetic rejected+``timed_out``
        decision so the workflow can take its timeout branch (AN-067).
        """
        decisions = self._decisions()
        if timeout is None:
            await workflow.wait_condition(lambda: gate_id in decisions)
            return decisions[gate_id]
        try:
            await workflow.wait_condition(lambda: gate_id in decisions, timeout=timeout)
        except TimeoutError:
            return ApprovalDecision(
                gate_id=gate_id, approved=False, comment="expired", timed_out=True
            )
        return decisions[gate_id]

    @workflow.signal(name="submit_decision")
    def submit_decision(self, decision: dict[str, Any]) -> None:
        """Deliver an approval/rejection to a waiting gate (see ``approval``)."""
        parsed = ApprovalDecision.model_validate(decision)
        self._decisions()[parsed.gate_id] = parsed
