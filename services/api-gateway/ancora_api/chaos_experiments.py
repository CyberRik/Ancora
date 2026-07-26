"""Chaos experiments: injection that asserts (Phase 5, RFC-0010).

The Chaos Lab already kills workers for real. An *experiment* wraps that in a
falsifiable claim: start a run, kill a worker while it is mid-flight, wait for the
fleet to recover, then **assert the invariants** (no lost state, no re-executed
activity, no double-fired effect) and **measure the recovery time**. The output is
a pass/fail, not a vibe — which is what makes it a regression test rather than a
demo.

This orchestrates existing pieces and adds no new failure machinery: it starts
runs through :class:`WorkflowService`, injects with the same
:class:`ChaosService.kill` the UI button uses, reads history back through the same
projection path the recovery view uses, and scores it with the pure checkers in
:mod:`chaos_invariants`. It lives in the API (which already holds the chaos socket
and the Temporal client) rather than a separate controller, so nothing is
duplicated.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from ancora_api import chaos_invariants as inv
from ancora_api.chaos import ChaosService
from ancora_api.schemas import (
    ChaosExperimentResultOut,
    ChaosScenarioOut,
    InvariantResultOut,
    StartRunRequest,
)
from ancora_api.service import WorkflowService
from ancora_common.db import session_scope
from ancora_common.models import Inbox

logger = logging.getLogger("ancora.chaos.experiments")


@dataclass(frozen=True)
class Scenario:
    """A repeatable fault-injection experiment and the invariants it must uphold."""

    name: str
    title: str
    description: str
    workflow: str
    workflow_input: dict[str, Any]
    fault_service: str
    invariants: tuple[str, ...]
    expected_rto_seconds: float
    # Some workflows park at a human gate; the experiment auto-advances them so it
    # can assert the run *completed* after recovery. (signal_name, arg).
    gate: tuple[str, dict[str, Any]] | None = None

    def to_out(self) -> ChaosScenarioOut:
        return ChaosScenarioOut(
            name=self.name,
            title=self.title,
            description=self.description,
            workflow=self.workflow,
            fault=f"SIGKILL a {self.fault_service} replica mid-run",
            invariants=list(self.invariants),
            expected_rto_seconds=self.expected_rto_seconds,
        )


# The scenario library. Scenarios target work on the *killable* activity-worker
# pool (3 replicas), so a single kill is survived by the others. They use nodes
# that heartbeat/redeliver within a bounded window — pipeline's async-completion
# activity deliberately is NOT here: it has a 10-minute start-to-close and cannot
# heartbeat (the work is off-process), so its recovery is timeout-bound and not a
# fast experiment.
SCENARIOS: dict[str, Scenario] = {
    "fan-out-failover": Scenario(
        name="fan-out-failover",
        title="Worker dies during a parallel fan-out",
        description=(
            "Run the research agent's search → 3-way summarize fan-out → synthesize, "
            "SIGKILL a worker mid-run, auto-approve the gate, and assert every branch "
            "completed exactly once with no state lost. Recovery is bounded by the node's "
            "start-to-close (a stranded activity is redelivered to a survivor)."
        ),
        workflow="research_agent",
        workflow_input={"topic": "chaos experiment"},
        fault_service="activity-worker",
        invariants=("no_lost_state", "no_reexecuted_activities", "exactly_once_effects"),
        expected_rto_seconds=60.0,
        gate=(
            "submit_decision",
            {"gate_id": "publish", "approved": True, "comment": "chaos auto-approve"},
        ),
    ),
}

_TERMINAL = frozenset({"Completed", "Failed", "Cancelled", "Terminated", "TimedOut"})


@dataclass
class ExperimentLog:
    """Recent experiment verdicts, kept in memory like the injection log."""

    limit: int = 20
    results: list[ChaosExperimentResultOut] = field(default_factory=list)

    def record(self, result: ChaosExperimentResultOut) -> None:
        self.results.append(result)
        del self.results[: max(0, len(self.results) - self.limit)]

    def recent(self) -> list[ChaosExperimentResultOut]:
        return list(reversed(self.results))


class ChaosExperimentRunner:
    """Runs a scenario end-to-end and scores it."""

    # Bounds so a wedged run can never hang the request forever. Recovery is
    # start-to-close-bound (a killed node is redelivered after ~60s), so the
    # window must clear that plus the remaining work and the gate.
    _INFLIGHT_TIMEOUT = 25.0
    _RECOVER_TIMEOUT = 160.0
    _POLL = 1.5

    def __init__(self, service: WorkflowService, chaos: ChaosService) -> None:
        self._service = service
        self._chaos = chaos

    async def run(self, name: str) -> ChaosExperimentResultOut:
        scenario = SCENARIOS[name]

        run = await self._service.start_run(
            scenario.workflow, StartRunRequest(input=scenario.workflow_input), None
        )
        run_id = run.id

        # 1. Wait until work is actually executing, so the kill lands mid-flight.
        await self._await_inflight(run_id)

        # 2. Inject the fault — the same real SIGKILL the UI button fires.
        kill_at = datetime.now(UTC)
        killed: str | None = None
        try:
            target = await self._chaos.kill(scenario.fault_service)
            killed = target.name
        except Exception as exc:  # noqa: BLE001 — a failed kill is a result, not a 500
            return self._result(
                scenario, run_id, [], None, None, "Running", note=f"kill failed: {exc}"
            )

        try:
            # 3. Drive to terminal within ONE bounded window: wait for recovery, and
            # auto-advance the gate once the run parks there (both under one deadline,
            # so the experiment can never run past _RECOVER_TIMEOUT).
            final_status = await self._drive_to_terminal(run_id, scenario)

            # 4. Score it against history + the inbox.
            events, status, wf_id = await self._service.get_run_execution(run_id)
            inbox = await self._inbox_for(wf_id)
            results = inv.evaluate(events, inbox)
            rto = inv.measure_rto(events, kill_at)
            return self._result(scenario, run_id, results, rto, killed, status or final_status)
        finally:
            # Restore the fleet so the experiment leaves the system as it found it —
            # otherwise repeated runs (a regression suite) deplete capacity.
            await self._restore(scenario.fault_service)

    async def _restore(self, service: str) -> None:
        try:
            await self._chaos.restart(service)
        except Exception as exc:  # noqa: BLE001 — best-effort cleanup
            logger.warning("fleet restore (restart %s) failed: %s", service, exc)

    async def _await_inflight(self, run_id: uuid.UUID) -> None:
        deadline = asyncio.get_event_loop().time() + self._INFLIGHT_TIMEOUT
        while asyncio.get_event_loop().time() < deadline:
            graph = await self._service.get_run_graph(run_id)
            if any(n.state in ("running", "retrying") for n in graph.nodes):
                return
            await asyncio.sleep(self._POLL)

    async def _drive_to_terminal(self, run_id: uuid.UUID, scenario: Scenario) -> str:
        """Poll to a terminal state, sending the gate decision once if the run parks."""
        deadline = asyncio.get_event_loop().time() + self._RECOVER_TIMEOUT
        signaled = False
        status = "Running"
        while asyncio.get_event_loop().time() < deadline:
            run = await self._service.get_run(run_id)
            status = run.status
            if status in _TERMINAL:
                return status
            if (
                scenario.gate is not None
                and not signaled
                and await self._gate_waiting(run.temporal_wf_id)
            ):
                signal, arg = scenario.gate
                try:
                    await self._service.signal_run(run_id, signal, arg)
                    signaled = True
                except Exception as exc:  # noqa: BLE001 — best-effort advance
                    logger.warning("gate advance signal failed: %s", exc)
            await asyncio.sleep(self._POLL)
        return status

    async def _gate_waiting(self, wf_id: str) -> bool:
        from ancora_common.models import ApprovalGate

        async with session_scope() as session:
            row = (
                await session.execute(
                    select(ApprovalGate.id).where(
                        ApprovalGate.temporal_wf_id == wf_id,
                        ApprovalGate.status == "waiting",
                    )
                )
            ).first()
        return row is not None

    async def _inbox_for(self, wf_id: str) -> list[inv.InboxRecord]:
        async with session_scope() as session:
            rows = (
                await session.execute(
                    select(Inbox.key, Inbox.status).where(Inbox.temporal_wf_id == wf_id)
                )
            ).all()
        return [inv.InboxRecord(key=str(k), status=str(s)) for k, s in rows]

    def _result(
        self,
        scenario: Scenario,
        run_id: uuid.UUID,
        results: list[inv.Invariant],
        rto: float | None,
        killed: str | None,
        final_status: str,
        *,
        note: str | None = None,
    ) -> ChaosExperimentResultOut:
        # Only score the invariants the scenario declared.
        declared = [r for r in results if r.name in scenario.invariants]
        passed = bool(declared) and all(r.passed for r in declared)
        return ChaosExperimentResultOut(
            scenario=scenario.name,
            run_id=run_id,
            passed=passed,
            rto_seconds=rto,
            expected_rto_seconds=scenario.expected_rto_seconds,
            invariants=[
                InvariantResultOut(
                    name=r.name, description=r.description, passed=r.passed, detail=r.detail
                )
                for r in declared
            ],
            killed=killed,
            final_status=final_status,
            note=note,
        )
