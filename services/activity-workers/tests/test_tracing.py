"""Distributed-tracing tests: the trace stays one tree across a process hop.

The claim 4c makes is that a run is one coherent execution even though it spans
an orchestrator, activity workers, and a Ray cluster — and that the trace proves
it. The failure mode is a *broken* trace: the compute runs in another
thread/process, loses the ambient context, and starts its own root, so the UI
shows two unrelated traces instead of one tree. These tests pin the propagation
that prevents that, using an in-memory exporter so no collector is involved.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from ancora_activity_worker.ray_bridge import LiveProgress, LocalBackend
from ancora_common.resources import ResourceSpec
from ancora_common.tracing import continue_trace, get_tracer, inject_carrier

# Install a real provider with an in-memory exporter once for the process. OTel
# only honours the first set_tracer_provider, so all tracing tests share it.
_EXPORTER = InMemorySpanExporter()
_provider = TracerProvider()
_provider.add_span_processor(SimpleSpanProcessor(_EXPORTER))
trace.set_tracer_provider(_provider)


@pytest.fixture(autouse=True)
def _clear_spans() -> Iterator[None]:
    _EXPORTER.clear()
    yield
    _EXPORTER.clear()


def _by_name() -> dict[str, object]:
    return {s.name: s for s in _EXPORTER.get_finished_spans()}


def test_inject_then_continue_keeps_one_trace() -> None:
    tracer = get_tracer()
    with tracer.start_as_current_span("driver") as driver:
        carrier = inject_carrier()
        driver_ctx = driver.get_span_context()

    assert "traceparent" in carrier

    # Simulate the receiving process: the ambient context is gone; only the
    # carrier remains. The re-attached span must join the driver's trace.
    with continue_trace(carrier, "compute.ray") as span:
        child_ctx = span.get_span_context()

    assert child_ctx.trace_id == driver_ctx.trace_id
    compute = _by_name()["compute.ray"]
    assert compute.parent is not None  # type: ignore[attr-defined]
    assert compute.parent.span_id == driver_ctx.span_id  # type: ignore[attr-defined]


def test_continue_trace_without_carrier_starts_new_trace() -> None:
    # No carrier and no ambient context → a valid new root span, not a crash.
    with continue_trace(None, "orphan") as span:
        ctx = span.get_span_context()
    assert ctx.trace_id != 0
    assert _by_name()["orphan"].parent is None  # type: ignore[attr-defined]


def test_local_backend_runs_compute_under_child_span() -> None:
    # The bridge must re-parent even the in-process thread-pool path: a pool
    # submit does not carry contextvars, so without the carrier the compute span
    # would orphan.
    backend = LocalBackend()

    def compute(_progress: LiveProgress) -> int:
        return 42

    tracer = get_tracer()
    with tracer.start_as_current_span("activity") as activity_span:
        activity_ctx = activity_span.get_span_context()
        handle = backend.submit(compute, resources=ResourceSpec(), progress=LiveProgress())
        assert handle.result() == 42

    compute_span = _by_name()["compute.local"]
    assert compute_span.parent is not None  # type: ignore[attr-defined]
    assert compute_span.parent.trace_id == activity_ctx.trace_id  # type: ignore[attr-defined]
    assert compute_span.parent.span_id == activity_ctx.span_id  # type: ignore[attr-defined]

    backend.shutdown()
