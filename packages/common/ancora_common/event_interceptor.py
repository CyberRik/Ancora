"""Worker interceptor that emits activity lifecycle events (Phase 4).

Temporal has no push: history is only readable by polling. To animate the DAG in
real time without hammering Temporal on the hot path, each worker wraps its own
activity executions and publishes a small event as work starts and finishes. This
is the *live* path; the durable projection is reconciled from history separately,
so a dropped event costs a few seconds of liveness, never correctness.

Why only activities, never workflows: a workflow interceptor runs inside the
deterministic sandbox, where a Redis write is a non-deterministic side effect that
would corrupt replay. Run-level lifecycle (started/completed/failed) is therefore
authored by the consumer's reconciler from history, not from here.

The event-building is a pure function (:func:`build_activity_event`) taking plain
fields, so the mapping is unit-tested without a Temporal worker in the loop.
"""

from __future__ import annotations

from typing import Any

from temporalio import activity
from temporalio.worker import (
    ActivityInboundInterceptor,
    ExecuteActivityInput,
    Interceptor,
)

from ancora_common.events import EventBus, EventKind, RunEvent, node_id_from_args


def build_activity_event(
    *,
    kind: str,
    wf_id: str,
    run_id: str,
    activity_id: str,
    activity_type: str,
    attempt: int,
    args: Any,
    worker_id: str,
    status: str | None = None,
    error: str | None = None,
) -> RunEvent:
    """Map an activity execution to a :class:`RunEvent` (pure)."""
    return RunEvent(
        kind=kind,
        wf_id=wf_id,
        run_id=run_id,
        node_id=node_id_from_args(args or ()),
        activity_id=activity_id,
        activity_type=activity_type,
        attempt=attempt,
        worker_id=worker_id or None,
        status=status,
        error=error,
    )


class _EventActivityInbound(ActivityInboundInterceptor):
    def __init__(
        self, next_interceptor: ActivityInboundInterceptor, bus: EventBus, worker_id: str
    ) -> None:
        super().__init__(next_interceptor)
        self._bus = bus
        self._worker_id = worker_id

    async def execute_activity(self, input: ExecuteActivityInput) -> Any:
        info = activity.info()

        def event(kind: str, status: str, error: str | None = None) -> RunEvent:
            return build_activity_event(
                kind=kind,
                wf_id=info.workflow_id or "",
                run_id=info.workflow_run_id or "",
                activity_id=info.activity_id,
                activity_type=info.activity_type,
                attempt=info.attempt,
                args=input.args,
                worker_id=self._worker_id,
                status=status,
                error=error,
            )

        await self._bus.publish(event(EventKind.ACTIVITY_STARTED, "Running"))
        try:
            result = await self.next.execute_activity(input)
        except Exception as exc:
            # Emit the failure event, then let Temporal see the raise so
            # retry/backoff is unchanged — the interceptor observes, never alters.
            await self._bus.publish(event(EventKind.ACTIVITY_FAILED, "Failed", str(exc)))
            raise
        await self._bus.publish(event(EventKind.ACTIVITY_COMPLETED, "Completed"))
        return result


class EventInterceptor(Interceptor):
    """Attach to a Temporal ``Worker`` to publish activity lifecycle events."""

    def __init__(self, bus: EventBus, worker_id: str) -> None:
        self._bus = bus
        self._worker_id = worker_id

    def intercept_activity(self, next: ActivityInboundInterceptor) -> ActivityInboundInterceptor:
        return _EventActivityInbound(next, self._bus, self._worker_id)
