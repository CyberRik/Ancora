"""Distributed tracing setup and cross-process context propagation (Phase 4c).

Ancora's value claim is that one workflow spans many processes — an orchestrator,
activity workers, and a Ray cluster — and stays one coherent execution. A trace
should tell the same story: a single tree from ``workflow → activity → ray
compute → provider call``, unbroken across every process hop.

Temporal's own OpenTelemetry interceptor carries context across the
workflow→activity hop (through Temporal headers). This module handles the two
hops Temporal does not know about:

* **The Ray boundary.** A compute function is pickled and shipped to another
  process; OTel's ambient context does not travel with it. So we inject the W3C
  ``traceparent`` into a plain dict, send it as data alongside the function, and
  re-extract it inside the Ray worker to parent the compute span correctly.

* **The provider call.** LLM/HTTP nodes open a child span so a slow provider is
  visible as its own segment of the run's trace.

Everything is a **no-op unless ``ANCORA_OTEL_ENDPOINT`` is set**: :func:`configure_tracing`
installs a real exporter only when there is a collector to export to, and the
propagation helpers work against whatever provider is installed (the default
no-op provider in tests). So importing this module never requires a collector.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

logger = logging.getLogger("ancora.tracing")

_TRACER_NAME = "ancora"
_propagator = TraceContextTextMapPropagator()

# Set once by configure_tracing so a second call (e.g. worker hot-reload) is cheap.
_configured = False


def configure_tracing(service_name: str, *, endpoint: str | None = None) -> bool:
    """Install an OTLP exporter for ``service_name`` if an endpoint is configured.

    Returns True if a real exporter was installed, False if tracing stays a no-op
    (no endpoint). Idempotent: safe to call on every process start / reload.
    """
    global _configured
    if _configured:
        return True
    if not endpoint:
        logger.debug("tracing disabled (no ANCORA_OTEL_ENDPOINT)")
        return False

    # Imported lazily so the no-op path never pulls the SDK/exporter.
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)
    _configured = True
    logger.info("tracing enabled for %s → %s", service_name, endpoint)
    return True


def get_tracer() -> trace.Tracer:
    return trace.get_tracer(_TRACER_NAME)


def inject_carrier() -> dict[str, str]:
    """Serialize the current span context into a W3C ``traceparent`` carrier.

    Call on the *driver* side before shipping work to another process. The result
    is a small ``str -> str`` dict safe to pickle and pass as an ordinary argument.
    """
    carrier: dict[str, str] = {}
    _propagator.inject(carrier)
    return carrier


@contextmanager
def continue_trace(carrier: Mapping[str, str] | None, name: str) -> Iterator[trace.Span]:
    """Re-attach a carrier and open a child span ``name`` (the receiving side).

    Used inside the Ray worker: extracting the carrier makes the compute span a
    child of the activity span that submitted it, so the trace stays one tree
    across the process boundary. With no carrier (or tracing disabled) this still
    yields a valid — possibly no-op — span, so callers need no special-casing.
    """
    ctx = _propagator.extract(carrier or {})
    tracer = get_tracer()
    with tracer.start_as_current_span(name, context=ctx) as span:
        yield span
