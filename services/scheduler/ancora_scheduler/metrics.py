"""Prometheus exposition for the scheduler (AN-047).

Two audiences read these metrics:

* **Operators**, asking "is my work stuck, and why?" — the admission counters
  break down by outcome and by the governor that made the call, so a wall of
  deferrals is immediately attributable to a rate limit, a watermark, or fairness.
* **Autoscalers**, asking "do I need more workers?" — ``queue_backlog`` and
  ``pending_demand`` are the signals an HPA/KEDA scaler or the Ray autoscaler
  keys off. Backlog is the queue's in-flight depth; pending demand is the part
  of it that is deferred and therefore *waiting on capacity we do not have*,
  which is the number a scaler should actually chase.

Rendering is hand-rolled rather than pulling in ``prometheus_client``: the
exposition format is a handful of lines, and the scheduler's counters already
live in plain dicts on the engine.
"""

from __future__ import annotations

from collections.abc import Iterable

from ancora_scheduler.engine import AdmissionEngine


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _labels(pairs: Iterable[tuple[str, str]]) -> str:
    rendered = ",".join(f'{k}="{_escape(v)}"' for k, v in pairs)
    return f"{{{rendered}}}" if rendered else ""


def _metric(
    name: str, kind: str, help_text: str, samples: Iterable[tuple[list[tuple[str, str]], float]]
) -> list[str]:
    lines = [f"# HELP {name} {help_text}", f"# TYPE {name} {kind}"]
    lines.extend(f"{name}{_labels(labels)} {value}" for labels, value in samples)
    return lines


def render(engine: AdmissionEngine) -> str:
    """Render the scheduler's current state in Prometheus text format."""
    stats = engine.stats
    counters = engine.inflight.counters()
    depths = engine.inflight.depths()
    lines: list[str] = []

    lines += _metric(
        "ancora_scheduler_admissions_total",
        "counter",
        "Admission decisions by outcome.",
        [([("outcome", outcome)], float(n)) for outcome, n in sorted(stats.by_outcome.items())],
    )
    lines += _metric(
        "ancora_scheduler_decisions_by_rule_total",
        "counter",
        "Admission decisions by the governor that decided them.",
        [([("rule", rule)], float(n)) for rule, n in sorted(stats.by_rule.items())],
    )
    lines += _metric(
        "ancora_scheduler_queue_decisions_total",
        "counter",
        "Admission decisions by task queue and outcome.",
        [
            ([("queue", q), ("outcome", o)], float(n))
            for (q, o), n in sorted(stats.by_queue_outcome.items())
        ],
    )
    lines += _metric(
        "ancora_scheduler_rate_limit_deferrals_total",
        "counter",
        "Deferrals caused by a provider rate limit.",
        [([("provider", p)], float(n)) for p, n in sorted(stats.rate_limit_defers.items())],
    )
    lines += _metric(
        "ancora_scheduler_queue_backlog",
        "gauge",
        "Work admitted to a task queue and not yet reported complete.",
        [([("queue", q)], float(n)) for q, n in sorted(depths.items())],
    )
    lines += _metric(
        "ancora_scheduler_pending_demand",
        "gauge",
        "Deferred work per queue — the autoscaling signal (capacity we lack).",
        [
            ([("queue", q)], float(n))
            for (q, outcome), n in sorted(stats.by_queue_outcome.items())
            if outcome == "defer"
        ],
    )
    lines += _metric(
        "ancora_scheduler_inflight_expired_total",
        "counter",
        "In-flight slots reclaimed by TTL because no completion was reported.",
        [([("queue", q)], float(n)) for q, n in sorted(counters["expired"].items())],
    )
    lines += _metric(
        "ancora_scheduler_lane_admissions_total",
        "counter",
        "Admissions by task queue and priority lane.",
        [
            ([("queue", q), ("priority", str(p))], float(n))
            for (q, p), n in sorted(engine.lanes.counts.items())
        ],
    )

    budget = engine.ledger.snapshot()
    lines += _metric(
        "ancora_scheduler_tenant_spend_usd",
        "gauge",
        "Recorded spend per tenant since scheduler start.",
        [([("tenant", t)], v) for t, v in sorted(budget["tenants"].items())],
    )
    return "\n".join(lines) + "\n"
