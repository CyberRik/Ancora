"""Ancora SDK — author durable AI workflows.

Authoring surface (Phase 1):

    from ancora import Workflow, workflow, activity

    @activity.defn(name="greet")
    async def greet(inp: GreetInput) -> GreetOutput: ...

    @workflow.defn(name="hello")
    class Hello(Workflow):
        @workflow.run
        async def run(self, params: dict) -> dict:
            out = await self.call(greet, GreetInput(name=params["name"]))
            return {"message": out.message}

``workflow`` and ``activity`` are re-exported from the Temporal SDK so authors get
the full, battle-tested decorator surface; :class:`Workflow` adds Ancora ergonomics
(``call``/``gather``) on top. Node objects and the built-in node library arrive in
Phase 3.
"""

from __future__ import annotations

from temporalio import activity, workflow

from ancora.base import Workflow
from ancora.version import __version__

__all__ = ["__version__", "Workflow", "workflow", "activity"]
