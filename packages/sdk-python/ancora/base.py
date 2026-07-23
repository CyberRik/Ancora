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
from temporalio.common import Priority, RetryPolicy

from ancora.nodes.idempotency import derive_idempotency_key
from ancora.policy import resolve_policy

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
        priority: Priority | None = None,
    ) -> _T:
        """Durably execute a single activity and return its result.

        Every completed call is an implicit checkpoint in Temporal history — if a
        worker dies, replay reconstructs everything up to here without re-running it.

        ``priority`` carries the lane and tenant key to Temporal's own queue
        ordering (AN-043), which is a layer below the scheduler's admission
        control: Temporal decides *what order* queued work is delivered in, the
        scheduler decides *whether* it should start at all.
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
        if priority is not None:
            kwargs["priority"] = priority

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
        start_to_close_timeout: timedelta | None = None,
        retry: RetryPolicy | None = None,
        policy: dict[str, Any] | None = None,
        tenant: str | None = None,
        deadline: timedelta | None = None,
    ) -> dict[str, Any]:
        """Execute a built-in node as an activity under its resolved policy.

        Routing, timeouts, retry shape, and priority come from
        :func:`ancora.policy.resolve_policy` — a pure function over the node type
        and the optional ``policy`` override, so every replay resolves identically
        (AN-039, AN-044). Explicit ``task_queue`` / ``start_to_close_timeout`` /
        ``retry`` arguments still win, for the cases the table does not cover.

        The idempotency key is derived deterministically from ``(workflow_id,
        node_id, input)`` (AN-062), so retries and replays of the same logical
        call reuse the same inbox entry and the node's side effect happens exactly
        once (AN-061).

        ``tenant`` and ``deadline`` are passed through to the scheduler for fair
        queuing (AN-042) and deadline enforcement (AN-046). ``deadline`` is the
        run's *total* budget, measured from workflow start — the remaining time is
        computed from ``workflow.now()``, which replay reproduces exactly.

        Returns ``{"output": <node output>, "cost": <cost>}``.
        """
        payload = (
            node_input.model_dump(mode="json") if isinstance(node_input, BaseModel) else node_input
        )
        info = workflow.info()
        key = derive_idempotency_key(
            workflow_id=info.workflow_id,
            node_id=node_id,
            payload=payload,
            override=idempotency_key,
        )

        remaining = self._deadline_remaining(deadline)
        resolved = resolve_policy(type_name, policy, deadline_remaining=remaining)

        req: dict[str, Any] = {
            "type_name": type_name,
            "node_id": node_id,
            "input": payload,
            "idempotency_key": key,
            "workflow_id": info.workflow_id,
            # Admission-control context; the worker forwards it to the scheduler.
            "scheduling": {
                "tenant": tenant or "default",
                "priority": resolved.priority,
                "task_queue": task_queue or resolved.task_queue,
                "deadline_seconds": remaining.total_seconds() if remaining is not None else None,
            },
        }
        result: dict[str, Any] = await self.call(
            "run_node",
            req,
            task_queue=task_queue or resolved.task_queue,
            start_to_close_timeout=start_to_close_timeout or resolved.start_to_close,
            schedule_to_close_timeout=resolved.schedule_to_close,
            heartbeat_timeout=resolved.heartbeat,
            retry=retry or resolved.retry.to_temporal(),
            priority=Priority(
                priority_key=resolved.priority,
                # Temporal's own fair-queuing key, so ordering *within* a queue is
                # tenant-aware even before the scheduler's governor sees the call.
                fairness_key=tenant or None,
            ),
        )
        return result

    def _deadline_remaining(self, deadline: timedelta | None) -> timedelta | None:
        """Time left on the run's deadline, from replay-safe workflow time.

        ``workflow.now()`` returns the deterministic clock Temporal reconstructs
        during replay, so this arithmetic yields the same answer on the original
        execution and every replay of it. Reading the wall clock here would make
        the resolved policy — and therefore the activity's timeouts — differ
        between runs, which is exactly the non-determinism replay forbids.
        """
        if deadline is None:
            return None
        elapsed = workflow.now() - workflow.info().start_time
        return deadline - elapsed

    # ---- durable human-in-the-loop ------------------------------------- #
    def _decisions(self) -> dict[str, ApprovalDecision]:
        """Lazily-initialized decision store, so subclasses need no ``super().__init__``."""
        store = self.__dict__.get("_ancora_decisions")
        if store is None:
            store = {}
            self.__dict__["_ancora_decisions"] = store
        return store

    async def approval(
        self,
        gate_id: str,
        *,
        timeout: timedelta | None = None,
        prompt: str = "",
        payload: dict[str, Any] | None = None,
        index: bool = True,
    ) -> ApprovalDecision:
        """Durably wait for a decision on ``gate_id`` (AN-055).

        Consumes zero compute while parked and survives worker restarts — the wait
        is a Temporal condition, resumed by the ``submit_decision`` signal. If
        ``timeout`` elapses first, returns a synthetic rejected+``timed_out``
        decision so the workflow can take its timeout branch (AN-067).

        With ``index`` set (the default) the gate is also written to the
        ``approval_gate`` projection so it appears in the approval inbox (AN-064).
        That write is a convenience, never a dependency: if the indexing activity
        is unavailable the gate still parks, still waits, and is still resolved by
        the signal — the human just has to find it another way.
        """
        decisions = self._decisions()
        if index:
            await self._index_gate("open", gate_id, prompt=prompt, payload=payload)

        decision: ApprovalDecision
        if timeout is None:
            await workflow.wait_condition(lambda: gate_id in decisions)
            decision = decisions[gate_id]
        else:
            try:
                await workflow.wait_condition(lambda: gate_id in decisions, timeout=timeout)
                decision = decisions[gate_id]
            except TimeoutError:
                decision = ApprovalDecision(
                    gate_id=gate_id, approved=False, comment="expired", timed_out=True
                )

        if index:
            await self._index_gate("close", gate_id, decision=decision)
        return decision

    async def _index_gate(
        self,
        action: str,
        gate_id: str,
        *,
        prompt: str = "",
        payload: dict[str, Any] | None = None,
        decision: ApprovalDecision | None = None,
    ) -> None:
        """Best-effort write to the approval-gate read model.

        Deliberately single-attempt and swallowed: the projection exists to make
        pending work discoverable, and a durable human gate must not be blocked by
        a bookkeeping table being briefly unreachable. The failure is recorded in
        history like any other activity failure, so it is visible after the fact.
        """
        arg: dict[str, Any] = {"gate_id": gate_id, "workflow_name": workflow.info().workflow_type}
        if action == "open":
            arg |= {"prompt": prompt, "payload": payload or {}}
        else:
            assert decision is not None
            arg |= {
                "approved": decision.approved,
                "timed_out": decision.timed_out,
                "comment": decision.comment,
                "decided_by": decision.decided_by,
            }
        try:
            await self.call(
                f"{action}_approval_gate",
                arg,
                start_to_close_timeout=timedelta(seconds=10),
                retry=RetryPolicy(maximum_attempts=1),
            )
        except Exception:  # noqa: BLE001 — the gate itself must not depend on this
            workflow.logger.warning("approval gate %s not indexed for %s", action, gate_id)

    @workflow.signal(name="submit_decision")
    def submit_decision(self, decision: dict[str, Any]) -> None:
        """Deliver an approval/rejection to a waiting gate (see ``approval``)."""
        parsed = ApprovalDecision.model_validate(decision)
        self._decisions()[parsed.gate_id] = parsed
