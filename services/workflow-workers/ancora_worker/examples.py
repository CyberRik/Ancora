"""Example workflows and activities registered by the Phase 1 worker.

These double as the demo and as the fixtures the integration/replay/durability
tests exercise. They intentionally use only deterministic workflow code; all work
happens in the ``greet`` activity.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from pydantic import BaseModel
from temporalio import activity as temporal_activity
from temporalio.common import RetryPolicy

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


@workflow.defn(name="research_agent")
class ResearchAgentWorkflow(Workflow):
    """The Phase-3 north-star example (AN-059): a durable research agent.

    ``search → summarize×N → synthesize → approve → publish`` built entirely from
    the built-in node library. LLM steps run against the CI mock provider (with a
    fallback provider configured), the approval gate waits durably for a human, and
    publish is an optional HTTP node. Kill any worker at any step and the run
    resumes from Temporal history with zero duplicated effects.
    """

    def __init__(self) -> None:
        self._at_gate = False

    @workflow.run
    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        topic = params.get("topic", "durable execution")
        n = int(params.get("summaries", 3))
        cpu_q = queue_for(Capability.CPU)
        io_q = queue_for(Capability.IO)
        providers = ["gemini"]

        def llm_input(prompt: str) -> dict[str, Any]:
            return {
                "messages": [{"role": "user", "content": prompt}],
                "model": "gemini-3.5-flash-lite",
                "providers": providers,
            }

        total_usd = 0.0

        # 1. Search for sources.
        search = await self.call_node(
            "llm", "search", llm_input(f"Find sources about {topic}"), task_queue=cpu_q
        )
        total_usd += float(search["cost"]["usd"])
        sources = search["output"]["text"]

        # 2. Summarize each source in parallel (fan-out / fan-in, AN-060).
        summaries = await self.gather(
            *[
                self.call_node(
                    "llm",
                    f"summarize-{i}",
                    llm_input(f"Summarize source {i}: {sources}"),
                    task_queue=cpu_q,
                )
                for i in range(n)
            ]
        )
        for s in summaries:
            total_usd += float(s["cost"]["usd"])
        summary_texts = [s["output"]["text"] for s in summaries]

        # 3. Synthesize a final report.
        synth = await self.call_node(
            "llm",
            "synthesize",
            llm_input(f"Synthesize a report on {topic} from: {' | '.join(summary_texts)}"),
            task_queue=cpu_q,
        )
        total_usd += float(synth["cost"]["usd"])
        report = synth["output"]["text"]

        # 4. Durable human approval before publishing.
        self._at_gate = True
        decision = await self.approval("publish")
        self._at_gate = False
        if not decision.approved:
            return {
                "status": "rejected",
                "report": report,
                "comment": decision.comment,
                "cost_usd": total_usd,
            }

        # 5. Publish via HTTP (idempotent) when a target is configured.
        published_status: int | None = None
        publish_url = params.get("publish_url")
        if publish_url:
            pub = await self.call_node(
                "http",
                "publish",
                {
                    "method": "POST",
                    "url": publish_url,
                    "json_body": {"topic": topic, "report": report},
                },
                task_queue=io_q,
            )
            published_status = int(pub["output"]["status"])

        return {
            "status": "published",
            "report": report,
            "sources": sources,
            "summaries": len(summary_texts),
            "published_status": published_status,
            "cost_usd": total_usd,
        }

    @workflow.query
    def at_gate(self) -> bool:
        return self._at_gate


class DemoInput(BaseModel):
    message: str


# Deliberate, human-watchable pacing so the failure and recovery are perceivable
# in the UI rather than flashing past. See the demo page for the matching timings.
_INGEST_SECONDS = 3.0
_PROCESS_WORK_SECONDS = 2.0  # how long attempt 1 "works" before it crashes
_PROCESS_SECONDS = 3.0
_EXPORT_SECONDS = 2.0
_RETRY_BACKOFF_SECONDS = 5.0  # visible "worker down, rescheduling" window


@activity.defn(name="ingest_dataset")
async def ingest_dataset(inp: DemoInput) -> dict[str, Any]:
    """Step 1 — pull in a real dataset. The expensive step we must never redo."""
    import httpx
    
    async with httpx.AsyncClient() as client:
        resp = await client.get("https://dummyjson.com/posts?limit=150")
        resp.raise_for_status()
        data = resp.json()
        
    posts = data.get("posts", [])
    return {"status": "ingested", "records": len(posts), "data": posts, "size": f"{len(resp.content)} bytes"}


@activity.defn(name="process_records")
async def process_records(inp: dict[str, Any]) -> dict[str, Any]:
    """Step 2 — process the records, but *fail the first attempt*.

    Attempt 1 does real CPU work and then crashes, so the UI can show real progress
    being lost to a mid-job failure (an OOM kill / eviction) with a retryable error.
    Temporal then waits a visible backoff and retries; the finished ingest step is
    replayed from history, not recomputed — exactly-once progress, no lost work.
    """
    import hashlib
    info = temporal_activity.info()
    posts = inp.get("data", [])
    
    def do_work() -> int:
        total_words = 0
        for post in posts:
            body = post.get("body", "")
            total_words += len(body.split())
            h = body.encode()
            for _ in range(5000):
                h = hashlib.sha256(h).digest()
        return total_words

    if info.attempt == 1:
        await asyncio.to_thread(do_work)  # Do some real work to take time
        print("💥 Simulating a worker failure while processing records (attempt 1)", flush=True)
        raise RuntimeError("Worker failure: out-of-memory while processing records")
        
    total_words = await asyncio.to_thread(do_work)
    
    return {
        "status": "processed",
        "records": len(posts),
        "total_words": total_words,
        "recovered_on_attempt": info.attempt,
    }


@activity.defn(name="export_results")
async def export_results(inp: dict[str, Any]) -> dict[str, Any]:
    """Step 3 — export the processed results."""
    await asyncio.sleep(_EXPORT_SECONDS)
    return {"status": "exported", "location": "s3://ancora-demo/results.parquet"}


@workflow.defn(name="durability_demo")
class DurabilityDemoWorkflow(Workflow):
    """A 3-step data pipeline that survives a mid-run worker failure.

    ingest → process (fails once) → export. When the process step fails, Temporal
    retries it while the already-finished ingest step is replayed from history —
    exactly-once progress, no lost work, no restart needed.
    """

    def __init__(self) -> None:
        self._status = "Starting..."

    @workflow.run
    async def run(self, params: dict[str, Any]) -> dict[str, Any]:
        self._status = "Ingesting dataset..."
        ingest = await self.call(
            ingest_dataset,
            DemoInput(message="start"),
            start_to_close_timeout=timedelta(seconds=30),
        )

        self._status = "Processing records (a worker will fail here)..."
        # Flat, visible backoff so the "worker down — rescheduling" window is
        # long enough to see (backoff_coefficient=1.0 keeps it a predictable 5s).
        retry = RetryPolicy(
            initial_interval=timedelta(seconds=_RETRY_BACKOFF_SECONDS),
            backoff_coefficient=1.0,
            maximum_attempts=5,
        )
        process = await self.call(
            process_records,
            ingest,
            retry=retry,
            start_to_close_timeout=timedelta(seconds=30),
        )

        self._status = "Exporting results..."
        export = await self.call(
            export_results,
            process,
            start_to_close_timeout=timedelta(seconds=30),
        )

        self._status = "Completed"
        return {
            "ingest": ingest,
            "process": process,
            "export": export,
            "message": "Pipeline finished despite a mid-run worker failure.",
        }

    @workflow.query
    def current_status(self) -> str:
        return self._status


# Registry consumed by the worker and the catalog reporter.
WORKFLOWS: list[type] = [
    HelloWorkflow,
    GatedWorkflow,
    PipelineWorkflow,
    ResearchAgentWorkflow,
    DurabilityDemoWorkflow,
]
ACTIVITIES: list[Callable[..., Any]] = [
    greet,
    ingest_dataset,
    process_records,
    export_results,
]
WORKFLOW_NAMES: dict[type, str] = {
    HelloWorkflow: "hello",
    GatedWorkflow: "gated",
    PipelineWorkflow: "pipeline",
    ResearchAgentWorkflow: "research_agent",
    DurabilityDemoWorkflow: "durability_demo",
}
