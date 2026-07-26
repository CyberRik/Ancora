"""Unit tests for the event bus contract and the activity-event mapping (Phase 4)."""

from __future__ import annotations

from datetime import UTC, datetime

from ancora_common.event_interceptor import build_activity_event
from ancora_common.events import (
    EventKind,
    RunEvent,
    decode_event,
    encode_event,
    node_id_from_args,
    run_stream,
)


def test_encode_decode_round_trip() -> None:
    event = RunEvent(
        kind=EventKind.ACTIVITY_COMPLETED,
        wf_id="wf-1",
        run_id="run-1",
        ts=datetime(2026, 7, 26, 12, 0, 0, tzinfo=UTC),
        node_id="summarize",
        activity_id="5",
        activity_type="run_node",
        attempt=2,
        worker_id="aw-host",
        status="Completed",
        payload={"backend": "local", "ray_task_id": None},
    )
    restored = decode_event(encode_event(event))
    assert restored == event


def test_encode_flattens_to_strings() -> None:
    # Redis stream fields must all be strings; None becomes "".
    fields = encode_event(RunEvent(kind=EventKind.ACTIVITY_STARTED, wf_id="wf", node_id=None))
    assert all(isinstance(v, str) for v in fields.values())
    assert fields["node_id"] == ""
    assert fields["attempt"] == "1"


def test_decode_tolerates_garbage_payload_and_attempt() -> None:
    # A corrupt tail must not crash the projector — decode degrades to defaults.
    ev = decode_event(
        {"kind": "activity.started", "wf_id": "wf", "attempt": "nope", "payload": "{bad"}
    )
    assert ev.attempt == 1
    assert ev.payload == {}


def test_node_id_from_args_reads_node_spec() -> None:
    assert node_id_from_args([{"node_id": "greet", "type": "llm"}]) == "greet"
    assert node_id_from_args([{"name": "fallback"}]) == "fallback"
    # A non-node activity (e.g. a gate bookkeeping call) has no node id.
    assert node_id_from_args(["just-a-string"]) is None
    assert node_id_from_args([]) is None


def test_run_stream_is_namespaced_per_run() -> None:
    assert run_stream("wf-abc") == "ancora:run:wf-abc"


def test_build_activity_event_maps_temporal_fields() -> None:
    event = build_activity_event(
        kind=EventKind.ACTIVITY_STARTED,
        wf_id="wf-9",
        run_id="run-9",
        activity_id="7",
        activity_type="run_node",
        attempt=3,
        args=[{"node_id": "synthesize"}],
        worker_id="aw-1",
        status="Running",
    )
    assert event.node_id == "synthesize"
    assert event.activity_id == "7"
    assert event.attempt == 3
    assert event.worker_id == "aw-1"
    assert event.status == "Running"


def test_build_activity_event_blank_worker_becomes_none() -> None:
    event = build_activity_event(
        kind=EventKind.ACTIVITY_FAILED,
        wf_id="wf",
        run_id="r",
        activity_id="1",
        activity_type="http_call",
        attempt=1,
        args=[],
        worker_id="",
        error="boom",
    )
    assert event.worker_id is None
    assert event.node_id is None
    assert event.error == "boom"
