"""Example workflows and activities registered by the Phase 1 worker.

These double as the demo and as the fixtures the integration/replay/durability
tests exercise. They intentionally use only deterministic workflow code; all work
happens in the ``greet`` activity.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from ancora import Workflow, activity, workflow


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


# Registry consumed by the worker and the catalog reporter.
WORKFLOWS: list[type] = [HelloWorkflow, GatedWorkflow]
ACTIVITIES = [greet]
WORKFLOW_NAMES: dict[type, str] = {HelloWorkflow: "hello", GatedWorkflow: "gated"}
