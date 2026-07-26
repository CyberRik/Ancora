"""Reconciler: keep run status authoritative from Temporal history.

The projector handles the *live* activity stream, but run-level lifecycle
(started/completed/failed) can't come from a worker interceptor — a workflow
emitting to Redis would break deterministic replay. So the reconciler is the
authoritative half: it periodically re-derives each non-terminal run's status
from Temporal (the source of truth) and settles the ``workflow_run`` projection.
This is also the heal path — if the live stream ever drops an event, the next
reconcile makes the projection correct again.

On a transition into a terminal state it also publishes a ``run.*`` event to the
bus, so a browser watching the run animates the ending in real time instead of
waiting for its next poll.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select
from temporalio.client import Client, WorkflowFailureError

from ancora_common.catalog import AncoraRunStatus, map_temporal_status
from ancora_common.db import session_scope
from ancora_common.events import EventBus, EventKind, RunEvent
from ancora_common.models import WorkflowRun
from ancora_event_consumer._util import sleep_or_stop
from ancora_event_consumer.settings import ConsumerSettings

logger = logging.getLogger("ancora.consumer.reconciler")

# Which terminal status maps to which run-level event.
_TERMINAL_EVENT = {
    AncoraRunStatus.COMPLETED: EventKind.RUN_COMPLETED,
    AncoraRunStatus.FAILED: EventKind.RUN_FAILED,
    AncoraRunStatus.CANCELLED: EventKind.RUN_CANCELED,
    AncoraRunStatus.TERMINATED: EventKind.RUN_CANCELED,
    AncoraRunStatus.TIMED_OUT: EventKind.RUN_FAILED,
}


class Reconciler:
    def __init__(self, client: Client, bus: EventBus, settings: ConsumerSettings) -> None:
        self._client = client
        self._bus = bus
        self._settings = settings

    async def reconcile_once(self) -> int:
        """Settle every non-terminal run; return how many transitioned to terminal."""
        async with session_scope() as session:
            rows = list(
                (
                    await session.execute(
                        select(WorkflowRun).where(
                            WorkflowRun.status.notin_(tuple(AncoraRunStatus.TERMINAL))
                        )
                    )
                )
                .scalars()
                .all()
            )
            settled = 0
            for run in rows:
                if await self._reconcile_run(run):
                    settled += 1
        return settled

    async def _reconcile_run(self, run: WorkflowRun) -> bool:
        """Update one run row from Temporal. Return True if it became terminal."""
        handle = self._client.get_workflow_handle(run.temporal_wf_id, run_id=run.temporal_run_id)
        try:
            desc = await handle.describe()
        except Exception as exc:  # noqa: BLE001 — a run may be gone/unreachable; retry next tick
            logger.debug("describe failed for %s: %s", run.temporal_wf_id, exc)
            return False

        status = map_temporal_status(int(desc.status)) if desc.status else run.status
        if status == run.status:
            return False

        run.status = status
        became_terminal = status in AncoraRunStatus.TERMINAL
        if became_terminal:
            run.closed_at = desc.close_time
            if status == AncoraRunStatus.COMPLETED:
                output = await handle.result()
                run.output = output if isinstance(output, dict) else {"result": output}
            elif status in (AncoraRunStatus.FAILED, AncoraRunStatus.TIMED_OUT):
                try:
                    await handle.result()
                except WorkflowFailureError as exc:
                    run.error = str(exc.cause or exc)
                except Exception as exc:  # noqa: BLE001
                    run.error = str(exc)

        await self._emit(run, became_terminal)
        return became_terminal

    async def _emit(self, run: WorkflowRun, terminal: bool) -> None:
        kind = _TERMINAL_EVENT.get(run.status) if terminal else EventKind.RUN_STARTED
        if kind is None:
            return
        await self._bus.publish(
            RunEvent(
                kind=kind,
                wf_id=run.temporal_wf_id,
                run_id=run.temporal_run_id,
                status=run.status,
                error=run.error,
            )
        )

    async def run_forever(self, stop: asyncio.Event) -> None:
        logger.info("reconciler started (every %.1fs)", self._settings.reconcile_interval_seconds)
        while not stop.is_set():
            try:
                settled = await self.reconcile_once()
                if settled:
                    logger.info("reconciled %d run(s) to terminal", settled)
            except Exception as exc:  # noqa: BLE001 — keep the loop alive across hiccups
                logger.warning("reconcile pass failed (retrying): %s", exc)
            await sleep_or_stop(stop, self._settings.reconcile_interval_seconds)
