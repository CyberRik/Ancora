"""Example workflows and activities registered by the Phase 1 worker.

These double as the demo and as the fixtures the integration/replay/durability
tests exercise. They intentionally use only deterministic workflow code; all work
happens in the ``greet`` activity.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from pydantic import BaseModel

from ancora import Workflow, activity, workflow
from ancora_common.resources import Capability, queue_for


class GreetInput(BaseModel):
    name: str


class GreetOutput(BaseModel):
    message: str


@activity.defn(name="greet")
async def greet(inp: GreetInput) -> GreetOutput:
    """A trivial activity. In Phase 2 this class of work is dispatched to Ray."""
    return GreetOutput(message=f"Hello, {inp.name}!")


@workflow.defn(name="hello")
class HelloWorkflow(Workflow):
    """Three sequential activities — the canonical durable-execution smoke test."""

    @workflow.run
    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name", "world")
        a = await self.call(greet, GreetInput(name=name))
        b = await self.call(greet, GreetInput(name=a.message))
        c = await self.call(greet, GreetInput(name=b.message))
        return {"message": c.message, "steps": 3}


@workflow.defn(name="gated")
class GatedWorkflow(Workflow):
    """Runs one activity, then durably waits for an ``approve`` signal, then runs
    another. Used by the worker-kill durability test: the process can die while the
    workflow waits and a fresh worker resumes it from history."""

    def __init__(self) -> None:
        self._approved = False
        self._at_gate = False

    @workflow.run
    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        first = await self.call(greet, GreetInput(name=params.get("name", "world")))
        self._at_gate = True
        await workflow.wait_condition(lambda: self._approved)
        second = await self.call(greet, GreetInput(name=first.message))
        return {"message": second.message}

    @workflow.signal
    def approve(self) -> None:
        self._approved = True

    @workflow.query
    def at_gate(self) -> bool:
        """True once the first activity is done and the workflow is waiting."""
        return self._at_gate


@workflow.defn(name="pipeline")
class PipelineWorkflow(Workflow):
    """Dispatches a 'GPU-ish' compute activity to the execution runtime (Phase 2).

    The activity (``ray_compute_async``) lives on the ``cpu`` capability queue and
    is served by the *activity* worker, which runs it on Ray (or the LocalBackend)
    via async completion — this workflow worker never touches Ray. Demonstrates the
    orchestration/execution split end-to-end.
    """

    @workflow.run
    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        req = {
            "label": params.get("label", "pipeline"),
            "batches": params.get("batches", 6),
            "batch_seconds": params.get("batch_seconds", 0.2),
        }
        result: dict[str, Any] = await self.call(
            "ray_compute_async",
            req,
            task_queue=queue_for(Capability.CPU),
            start_to_close_timeout=timedelta(minutes=10),
        )
        return {"compute": result, "steps": 1}


# Registry consumed by the worker and the catalog reporter.
WORKFLOWS: list[type] = [HelloWorkflow, GatedWorkflow, PipelineWorkflow]
ACTIVITIES = [greet]
WORKFLOW_NAMES: dict[type, str] = {
    HelloWorkflow: "hello",
    GatedWorkflow: "gated",
    PipelineWorkflow: "pipeline",
}
