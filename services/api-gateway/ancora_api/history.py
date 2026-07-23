"""Decoding helpers shared by the views that read a Temporal workflow history.

Two views read the same event stream and need the same three things out of it:
timestamps that distinguish "unset" from "the epoch", durations that distinguish
"unset" from "zero", and the node's *own* name — which is not where you would
expect it to be.

:mod:`ancora_api.recovery` reads history as a time axis (what a worker death did).
:mod:`ancora_api.graph` reads the same history as a shape (what the run's DAG is).
Both are pure functions of the event list, so both are unit-testable without a
server; these helpers are the seam they share.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from google.protobuf.duration_pb2 import Duration
from google.protobuf.timestamp_pb2 import Timestamp
from temporalio.api.common.v1 import Payloads


def dt(ts: Timestamp | None) -> datetime | None:
    """Proto timestamp → aware datetime, treating the zero value as unset.

    Protobuf has no null, so an absent time arrives as 1970-01-01. Passing that
    through would draw a span reaching back fifty years.
    """
    if ts is None or (ts.seconds == 0 and ts.nanos == 0):
        return None
    return ts.ToDatetime().replace(tzinfo=UTC)


def secs(d: Duration | None) -> float | None:
    """Proto duration → seconds, treating zero as unset (Temporal's own convention)."""
    if d is None:
        return None
    value = d.ToTimedelta().total_seconds()
    return value or None


def decode_input(payloads: Payloads | None) -> dict[str, Any] | None:
    """Best-effort decode of an activity's first input payload as a JSON object.

    The default data converter writes ``encoding: json/plain``, so one decode
    recovers the argument the workflow actually passed. Anything else — a custom
    converter, an encrypted payload, a non-object argument — returns ``None``
    rather than raising: these views degrade to coarser labels, they never fail
    because a payload was not shaped the way they hoped.
    """
    if payloads is None or not payloads.payloads:
        return None
    p = payloads.payloads[0]
    if p.metadata.get("encoding") != b"json/plain":
        return None
    try:
        decoded = json.loads(p.data)
    except (ValueError, UnicodeDecodeError):
        return None
    return decoded if isinstance(decoded, dict) else None


def _str_field(decoded: dict[str, Any] | None, key: str) -> str | None:
    if not decoded:
        return None
    value = decoded.get(key)
    return value if isinstance(value, str) and value else None


def node_id_from_input(payloads: Payloads | None) -> str | None:
    """Pull the node id out of a scheduled ``run_node`` activity's input.

    Temporal assigns activity ids by sequence — "1", "2", "3" — so the id alone
    labels a chart with numbers nobody can act on. The name the author gave the
    node ("search", "summarize-0") is in the activity's own input.
    """
    return _str_field(decode_input(payloads), "node_id")


def node_type_from_input(payloads: Payloads | None) -> str | None:
    """The built-in node class (``llm``, ``http``, …) a ``run_node`` call selected."""
    return _str_field(decode_input(payloads), "type_name")


def node_label(activity_type: str, activity_id: str, decoded: str | None = None) -> str:
    """A human label for an activity: its node name where one can be recovered.

    Falls back to the activity type (meaningful for the fixed-purpose activities
    like ``open_approval_gate``) and finally to the raw activity id.
    """
    if decoded:
        return decoded
    return activity_id if activity_type == "run_node" else activity_type
