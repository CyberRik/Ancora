"""Reconstructing a run's DAG from Temporal's record.

The graph makes a structural claim that is easy to get wrong and impossible to
eyeball: *these three steps ran at the same time, that one waited for them.* Get
it wrong and the picture is confidently, silently misleading — a fan-out drawn
as a chain looks perfectly reasonable to someone who has never seen the code.

So the layering is pinned here against synthetic histories. The load-bearing
fact is that layers come from ``workflow_task_completed_event_id`` — Temporal's
own record of which decision commanded which activity — and not from timestamps.
Two activities that happen to start a millisecond apart are *not* evidence of
concurrency; being scheduled by one workflow task is.

The other half is state. A node that died with its worker, a node parked at a
gate, and a node nobody has picked up all render differently, and each of those
readings has a way of being wrong that history alone does not reveal.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from google.protobuf.duration_pb2 import Duration
from google.protobuf.timestamp_pb2 import Timestamp
from temporalio.api.enums.v1 import EventType, PendingActivityState
from temporalio.api.history.v1 import HistoryEvent
from temporalio.api.workflow.v1 import PendingActivityInfo

from ancora_api.graph import build_graph
from ancora_api.schemas import GraphNodeOut, RunGraphOut

RUN_ID = uuid.UUID("00000000-0000-0000-0000-0000000000cd")
T0 = datetime(2026, 7, 23, 12, 0, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #
def ts(offset_seconds: float) -> Timestamp:
    stamp = Timestamp()
    stamp.FromDatetime(T0 + timedelta(seconds=offset_seconds))
    return stamp


def secs(value: float) -> Duration:
    d = Duration()
    d.FromTimedelta(timedelta(seconds=value))
    return d


_next_id = iter(range(1, 10_000))


def wf_started(*, name: str = "research_agent", at: float = 0.0) -> HistoryEvent:
    e = HistoryEvent(event_id=next(_next_id), event_time=ts(at))
    e.event_type = EventType.EVENT_TYPE_WORKFLOW_EXECUTION_STARTED
    e.workflow_execution_started_event_attributes.workflow_type.name = name
    return e


def decision(*, at: float = 0.0) -> HistoryEvent:
    """A ``WorkflowTaskCompleted`` — the event activities are attributed to."""
    e = HistoryEvent(event_id=next(_next_id), event_time=ts(at))
    e.event_type = EventType.EVENT_TYPE_WORKFLOW_TASK_COMPLETED
    return e


def scheduled(
    activity_id: str,
    *,
    by: HistoryEvent,
    at: float,
    activity_type: str = "run_node",
    node_id: str | None = None,
    node_type: str = "llm",
    queue: str = "ancora-cpu",
    priority: str = "standard",
    payload: dict[str, object] | None = None,
) -> HistoryEvent:
    e = HistoryEvent(event_id=next(_next_id), event_time=ts(at))
    e.event_type = EventType.EVENT_TYPE_ACTIVITY_TASK_SCHEDULED
    a = e.activity_task_scheduled_event_attributes
    a.activity_id = activity_id
    a.activity_type.name = activity_type
    a.task_queue.name = queue
    a.start_to_close_timeout.CopyFrom(secs(300.0))
    a.workflow_task_completed_event_id = by.event_id
    body = payload
    if body is None and node_id is not None:
        body = {
            "type_name": node_type,
            "node_id": node_id,
            "scheduling": {"tenant": "default", "priority": priority},
        }
    if body is not None:
        p = a.input.payloads.add()
        p.metadata["encoding"] = b"json/plain"
        p.data = json.dumps(body).encode()
    return e


def started(sched: HistoryEvent, *, at: float, identity: str, attempt: int = 1) -> HistoryEvent:
    e = HistoryEvent(event_id=next(_next_id), event_time=ts(at))
    e.event_type = EventType.EVENT_TYPE_ACTIVITY_TASK_STARTED
    a = e.activity_task_started_event_attributes
    a.scheduled_event_id = sched.event_id
    a.identity = identity
    a.attempt = attempt
    return e


def completed(sched: HistoryEvent, *, at: float) -> HistoryEvent:
    e = HistoryEvent(event_id=next(_next_id), event_time=ts(at))
    e.event_type = EventType.EVENT_TYPE_ACTIVITY_TASK_COMPLETED
    e.activity_task_completed_event_attributes.scheduled_event_id = sched.event_id
    return e


def failed(sched: HistoryEvent, *, at: float, message: str) -> HistoryEvent:
    e = HistoryEvent(event_id=next(_next_id), event_time=ts(at))
    e.event_type = EventType.EVENT_TYPE_ACTIVITY_TASK_FAILED
    a = e.activity_task_failed_event_attributes
    a.scheduled_event_id = sched.event_id
    a.failure.message = message
    return e


def timer_started(timer_id: str, *, at: float, fires_after: float) -> HistoryEvent:
    e = HistoryEvent(event_id=next(_next_id), event_time=ts(at))
    e.event_type = EventType.EVENT_TYPE_TIMER_STARTED
    a = e.timer_started_event_attributes
    a.timer_id = timer_id
    a.start_to_fire_timeout.CopyFrom(secs(fires_after))
    return e


def pending_started(
    activity_id: str, *, started_at: float, identity: str, attempt: int = 1
) -> PendingActivityInfo:
    pa = PendingActivityInfo(activity_id=activity_id, attempt=attempt)
    pa.activity_type.name = "run_node"
    pa.state = PendingActivityState.PENDING_ACTIVITY_STATE_STARTED
    pa.last_started_time.CopyFrom(ts(started_at))
    pa.last_worker_identity = identity
    return pa


def pending_scheduled(
    activity_id: str, *, attempt: int = 1, last_failure: str = ""
) -> PendingActivityInfo:
    pa = PendingActivityInfo(activity_id=activity_id, attempt=attempt)
    pa.activity_type.name = "run_node"
    pa.state = PendingActivityState.PENDING_ACTIVITY_STATE_SCHEDULED
    if last_failure:
        pa.last_failure.message = last_failure
    return pa


def gate_open(gate_id: str, *, by: HistoryEvent, at: float) -> HistoryEvent:
    return scheduled(
        f"gate-open-{gate_id}",
        by=by,
        at=at,
        activity_type="open_approval_gate",
        payload={"gate_id": gate_id, "workflow_name": "research_agent", "prompt": ""},
    )


def gate_close(
    gate_id: str,
    *,
    by: HistoryEvent,
    at: float,
    approved: bool = True,
    timed_out: bool = False,
    decided_by: str = "",
    comment: str = "",
) -> HistoryEvent:
    return scheduled(
        f"gate-close-{gate_id}",
        by=by,
        at=at,
        activity_type="close_approval_gate",
        payload={
            "gate_id": gate_id,
            "workflow_name": "research_agent",
            "approved": approved,
            "timed_out": timed_out,
            "decided_by": decided_by,
            "comment": comment,
        },
    )


def build(
    events: Sequence[HistoryEvent],
    *,
    pending: Sequence[PendingActivityInfo] = (),
    status: str = "Running",
    terminal: bool = False,
    has_pending_workflow_task: bool = False,
) -> RunGraphOut:
    return build_graph(
        run_id=RUN_ID,
        workflow_name="research_agent",
        status=status,
        terminal=terminal,
        events=list(events),
        pending_activities=list(pending),
        has_pending_workflow_task=has_pending_workflow_task,
        now=T0 + timedelta(seconds=600),
    )


def by_label(graph: RunGraphOut, label: str) -> GraphNodeOut:
    return next(n for n in graph.nodes if n.label == label)


# --------------------------------------------------------------------------- #
# Layering — the structural claim
# --------------------------------------------------------------------------- #
def test_a_fan_out_is_one_layer_and_a_chain_is_many() -> None:
    """The shape claim: three summaries ran together, the synthesis waited.

    This is the whole reason the view exists. If a fan-out collapses into a
    chain the picture is wrong in the direction that flatters the system — it
    looks like careful sequencing rather than concurrency.
    """
    d1, d2, d3 = decision(at=0), decision(at=10), decision(at=40)
    search = scheduled("1", by=d1, at=1, node_id="search")
    sums = [scheduled(str(i + 2), by=d2, at=11, node_id=f"summarize-{i}") for i in range(3)]
    synth = scheduled("5", by=d3, at=41, node_id="synthesize")

    graph = build([wf_started(), d1, search, d2, *sums, d3, synth])

    assert by_label(graph, "search").layer == 0
    assert {by_label(graph, f"summarize-{i}").layer for i in range(3)} == {1}
    assert by_label(graph, "synthesize").layer == 2


def test_concurrency_comes_from_the_commanding_decision_not_from_timestamps() -> None:
    """Two activities scheduled a millisecond apart are not evidence of a fan-out.

    A timing heuristic would merge these; Temporal's causality does not. The
    workflow only decided on the second after seeing the first finish.
    """
    d1, d2 = decision(at=0), decision(at=0)
    first = scheduled("1", by=d1, at=0.0, node_id="first")
    second = scheduled("2", by=d2, at=0.001, node_id="second")

    graph = build([wf_started(), d1, first, d2, second])

    assert by_label(graph, "first").layer == 0
    assert by_label(graph, "second").layer == 1


def test_edges_join_consecutive_layers_and_mark_the_finished_side() -> None:
    d1, d2 = decision(at=0), decision(at=10)
    search = scheduled("1", by=d1, at=1, node_id="search")
    sums = [scheduled(str(i + 2), by=d2, at=11, node_id=f"summarize-{i}") for i in range(2)]

    graph = build(
        [
            wf_started(),
            d1,
            search,
            started(search, at=1, identity="w"),
            completed(search, at=9),
            d2,
            *sums,
        ]
    )

    src = by_label(graph, "search").id
    assert {e.target for e in graph.edges if e.source == src} == {
        by_label(graph, "summarize-0").id,
        by_label(graph, "summarize-1").id,
    }
    # The source has a recorded result, so the edge is "already flowed" — the UI
    # animates only the edges whose upstream has not produced anything yet.
    assert all(e.done for e in graph.edges)


def test_layers_are_compacted_so_a_gap_does_not_become_an_empty_column() -> None:
    """Raw layer keys are event ids: sparse, and sparser once gates absorb their
    bookkeeping activities. Ranks must come out consecutive regardless."""
    d1, d2 = decision(at=0), decision(at=50)
    a = scheduled("1", by=d1, at=1, node_id="a")
    b = scheduled("2", by=d2, at=51, node_id="b")

    graph = build([wf_started(), d1, a, decision(at=25), d2, b])

    assert sorted(n.layer for n in graph.nodes) == [0, 1]


# --------------------------------------------------------------------------- #
# Labels
# --------------------------------------------------------------------------- #
def test_nodes_are_labelled_by_name_not_by_temporal_sequence_number() -> None:
    d1 = decision(at=0)
    graph = build([wf_started(), d1, scheduled("1", by=d1, at=1, node_id="search")])

    node = graph.nodes[0]
    assert node.label == "search"
    assert node.kind == "node"
    assert node.node_type == "llm"
    assert node.activity_id == "1"


def test_plain_activities_fall_back_to_their_activity_type() -> None:
    d1 = decision(at=0)
    graph = build(
        [wf_started(name="hello"), d1, scheduled("1", by=d1, at=1, activity_type="greet")]
    )

    assert graph.nodes[0].label == "greet"
    assert graph.nodes[0].kind == "activity"


def test_repeated_labels_are_numbered_so_a_chain_stays_tellable_apart() -> None:
    """``hello`` calls the same activity three times; three boxes reading "greet"
    would make the graph useless for saying *which* step failed."""
    d1, d2, d3 = decision(at=0), decision(at=1), decision(at=2)
    graph = build(
        [
            wf_started(name="hello"),
            d1,
            scheduled("1", by=d1, at=1, activity_type="greet"),
            d2,
            scheduled("2", by=d2, at=2, activity_type="greet"),
            d3,
            scheduled("3", by=d3, at=3, activity_type="greet"),
        ]
    )

    assert [n.label for n in graph.nodes] == ["greet #1", "greet #2", "greet #3"]


def test_distinct_labels_are_left_alone() -> None:
    d1, d2 = decision(at=0), decision(at=1)
    graph = build(
        [
            wf_started(),
            d1,
            scheduled("1", by=d1, at=1, node_id="search"),
            d2,
            scheduled("2", by=d2, at=2, node_id="synthesize"),
        ]
    )

    assert sorted(n.label for n in graph.nodes) == ["search", "synthesize"]


def test_an_undecodable_payload_degrades_to_the_activity_id() -> None:
    """A custom or encrypted converter must cost the label, not the view."""
    d1 = decision(at=0)
    sched = scheduled("7", by=d1, at=1)
    p = sched.activity_task_scheduled_event_attributes.input.payloads.add()
    p.metadata["encoding"] = b"binary/encrypted"
    p.data = b"\x00\x01"

    graph = build([wf_started(), d1, sched])

    assert graph.nodes[0].label == "7"
    assert graph.nodes[0].node_type is None


# --------------------------------------------------------------------------- #
# Node state
# --------------------------------------------------------------------------- #
def test_an_attempt_in_flight_is_running_even_though_history_has_no_start_event() -> None:
    """Temporal writes ``ActivityTaskStarted`` only at an attempt's terminal
    event, so the attempt happening *right now* — including one stranded on a
    dead worker — is absent from history. Reading history alone draws it as
    though nobody had picked it up, which is backwards."""
    d1 = decision(at=0)
    sched = scheduled("1", by=d1, at=1, node_id="search")

    graph = build(
        [wf_started(), d1, sched],
        pending=[pending_started("1", started_at=2, identity="7@host")],
    )

    node = by_label(graph, "search")
    assert node.state == "running"
    assert node.worker == "7@host"


def test_a_node_scheduled_with_nobody_polling_is_queued_not_running() -> None:
    d1 = decision(at=0)
    graph = build(
        [wf_started(), d1, scheduled("1", by=d1, at=1, node_id="search")],
        pending=[pending_scheduled("1")],
    )

    assert by_label(graph, "search").state == "queued"


def test_a_second_attempt_waiting_to_start_is_retrying_not_queued() -> None:
    """ "Queued" and "retrying" both mean "not running", and only one of them
    means something already went wrong."""
    d1 = decision(at=0)
    graph = build(
        [wf_started(), d1, scheduled("1", by=d1, at=1, node_id="search")],
        pending=[pending_scheduled("1", attempt=2, last_failure="worker died")],
    )

    node = by_label(graph, "search")
    assert node.state == "retrying"
    assert node.attempts == 2
    assert node.failure == "worker died"


def test_retries_collapse_into_one_vertex_carrying_the_attempt_count() -> None:
    """A retry reuses its scheduled event, so a node that died and came back is
    one vertex — not two boxes implying the work happened twice."""
    d1 = decision(at=0)
    sched = scheduled("1", by=d1, at=1, node_id="search")

    graph = build(
        [
            wf_started(),
            d1,
            sched,
            started(sched, at=302, identity="9@host", attempt=2),
            completed(sched, at=310),
        ]
    )

    node = by_label(graph, "search")
    assert len([n for n in graph.nodes if n.label == "search"]) == 1
    assert node.state == "completed"
    assert node.attempts == 2
    assert node.lost_attempts == 1
    assert node.note is not None and "attempt" in node.note.lower()


def test_a_failure_keeps_its_message() -> None:
    d1 = decision(at=0)
    sched = scheduled("1", by=d1, at=1, node_id="fetch", node_type="http")

    graph = build(
        [
            wf_started(),
            d1,
            sched,
            started(sched, at=2, identity="w"),
            failed(sched, at=5, message="502 from provider"),
        ]
    )

    node = by_label(graph, "fetch")
    assert node.state == "failed"
    assert node.failure == "502 from provider"
    assert node.duration_seconds == 3.0


def test_scheduling_context_survives_onto_the_vertex() -> None:
    d1 = decision(at=0)
    graph = build(
        [
            wf_started(),
            d1,
            scheduled("1", by=d1, at=1, node_id="search", queue="ancora-gpu", priority="bulk"),
        ]
    )

    node = by_label(graph, "search")
    assert node.queue == "ancora-gpu"
    assert node.priority == "bulk"


# --------------------------------------------------------------------------- #
# Approval gates
# --------------------------------------------------------------------------- #
def test_an_open_gate_with_no_close_is_a_run_parked_there() -> None:
    d1 = decision(at=0)
    op = gate_open("publish", by=d1, at=1)

    graph = build([wf_started(), d1, op, started(op, at=1, identity="w"), completed(op, at=2)])

    gate = by_label(graph, "publish")
    assert gate.kind == "gate"
    assert gate.state == "waiting"
    assert gate.note is not None and "durable" in gate.note


def test_the_gates_bookkeeping_activities_do_not_get_vertices_of_their_own() -> None:
    """A reader cares about the gate, not about the two projection writes that
    bracket it."""
    d1, d2 = decision(at=0), decision(at=100)
    op = gate_open("publish", by=d1, at=1)
    cl = gate_close("publish", by=d2, at=101, decided_by="ritankar")

    graph = build([wf_started(), d1, op, completed(op, at=2), d2, cl])

    assert [n.label for n in graph.nodes] == ["publish"]
    assert not any(
        n.activity_type in ("open_approval_gate", "close_approval_gate") for n in graph.nodes
    )


def test_a_closed_gate_carries_its_decision() -> None:
    d1, d2 = decision(at=0), decision(at=100)
    op = gate_open("publish", by=d1, at=1)
    cl = gate_close("publish", by=d2, at=101, decided_by="ritankar", comment="ship it")

    graph = build([wf_started(), d1, op, completed(op, at=2), d2, cl])

    gate = by_label(graph, "publish")
    assert gate.state == "completed"
    assert gate.approved is True
    assert gate.decided_by == "ritankar"
    assert gate.note is not None and "ship it" in gate.note
    assert gate.duration_seconds == 99.0


def test_an_expired_gate_is_recorded_as_a_decision_not_a_failure() -> None:
    """Nobody deciding *is* a decision — the workflow took its timeout branch and
    kept running. Painting that red would be a lie about what happened."""
    d1, d2 = decision(at=0), decision(at=100)
    op = gate_open("publish", by=d1, at=1)
    cl = gate_close("publish", by=d2, at=101, approved=False, timed_out=True)

    graph = build([wf_started(), d1, op, completed(op, at=2), d2, cl])

    gate = by_label(graph, "publish")
    assert gate.state == "completed"
    assert gate.timed_out is True
    assert gate.note is not None and "expired" in gate.note


def test_a_gate_still_open_when_the_run_ended_never_got_its_decision() -> None:
    d1 = decision(at=0)
    op = gate_open("publish", by=d1, at=1)

    graph = build(
        [wf_started(), d1, op, completed(op, at=2)],
        status="Terminated",
        terminal=True,
    )

    gate = by_label(graph, "publish")
    assert gate.state == "canceled"
    assert gate.note is not None and "no decision" in gate.note


def test_a_gate_whose_inbox_write_failed_still_shows_as_waiting() -> None:
    """Indexing is deliberately best-effort: the gate must not depend on a
    bookkeeping table. Failing to say so would send someone hunting the inbox for
    a row that is not there."""
    d1 = decision(at=0)
    op = gate_open("publish", by=d1, at=1)

    graph = build([wf_started(), d1, op, failed(op, at=2, message="db unreachable")])

    gate = by_label(graph, "publish")
    assert gate.state == "waiting"
    assert gate.note is not None and "inbox" in gate.note


def test_reopening_the_same_gate_id_yields_two_vertices_in_order() -> None:
    """A workflow that loops over a gate must not have its second wait folded
    into the first."""
    d1, d2, d3 = decision(at=0), decision(at=100), decision(at=200)
    op1 = gate_open("review", by=d1, at=1)
    cl1 = gate_close("review", by=d2, at=101, approved=False)
    op2 = gate_open("review", by=d3, at=201)

    graph = build(
        [wf_started(), d1, op1, completed(op1, at=2), d2, cl1, d3, op2, completed(op2, at=202)]
    )

    gates = sorted((n for n in graph.nodes if n.kind == "gate"), key=lambda n: n.layer)
    assert len(gates) == 2
    assert gates[0].state == "completed"
    assert gates[0].approved is False
    assert gates[1].state == "waiting"


def test_a_waiting_gate_with_a_running_timer_says_when_it_expires() -> None:
    d1 = decision(at=0)
    op = gate_open("publish", by=d1, at=1)

    graph = build(
        [wf_started(), d1, op, completed(op, at=2), timer_started("t1", at=2, fires_after=86400)]
    )

    gate = by_label(graph, "publish")
    assert gate.ended_at == T0 + timedelta(seconds=2 + 86400)
    assert gate.note is not None and "expires" in gate.note


# --------------------------------------------------------------------------- #
# The unmarked durable wait
# --------------------------------------------------------------------------- #
def test_a_workflow_parked_on_a_bare_signal_gets_a_wait_vertex() -> None:
    """Not every wait is bracketed by gate activities — a workflow may just await
    a signal, which history records as the absence of anything. Without a vertex
    the DAG simply stops, which reads as broken rather than as parked."""
    d1, d2 = decision(at=0), decision(at=10)
    a = scheduled("1", by=d1, at=1, activity_type="greet")

    graph = build([wf_started(name="gated"), d1, a, completed(a, at=2), d2])

    wait = by_label(graph, "durable wait")
    assert wait.kind == "wait"
    assert wait.state == "waiting"
    assert wait.layer == 1


def test_no_wait_vertex_while_a_step_is_still_in_flight() -> None:
    """A run mid-step is not parked, and a phantom "waiting" box would say the
    opposite of what is happening."""
    d1 = decision(at=0)
    a = scheduled("1", by=d1, at=1, node_id="search")

    graph = build([wf_started(), d1, a], pending=[pending_started("1", started_at=2, identity="w")])

    assert not any(n.kind == "wait" for n in graph.nodes)


def test_no_wait_vertex_when_a_gate_already_marks_the_parking_spot() -> None:
    d1 = decision(at=0)
    op = gate_open("publish", by=d1, at=1)

    graph = build([wf_started(), d1, op, completed(op, at=2), decision(at=3)])

    assert not any(n.kind == "wait" for n in graph.nodes)
    assert by_label(graph, "publish").state == "waiting"


def test_no_wait_vertex_on_a_finished_run() -> None:
    d1 = decision(at=0)
    a = scheduled("1", by=d1, at=1, activity_type="greet")

    graph = build(
        [wf_started(name="hello"), d1, a, completed(a, at=2), decision(at=3)],
        status="Completed",
        terminal=True,
    )

    assert not any(n.kind == "wait" for n in graph.nodes)


def test_no_wait_vertex_while_a_workflow_task_is_outstanding() -> None:
    """Between the decision and its commands there is a moment where history
    looks parked. It is not — a worker is holding the orchestration step."""
    d1, d2 = decision(at=0), decision(at=10)
    a = scheduled("1", by=d1, at=1, activity_type="greet")

    graph = build(
        [wf_started(name="gated"), d1, a, completed(a, at=2), d2],
        has_pending_workflow_task=True,
    )

    assert not any(n.kind == "wait" for n in graph.nodes)


# --------------------------------------------------------------------------- #
# Whole-graph properties
# --------------------------------------------------------------------------- #
def test_an_empty_history_is_an_empty_graph_not_an_invented_one() -> None:
    """A run that has not scheduled anything has no graph. Drawing the workflow's
    *declared* shape here would show steps that may never happen."""
    graph = build([wf_started()])

    assert graph.nodes == []
    assert graph.edges == []
    assert graph.total == 0


def test_the_denominator_counts_only_what_the_workflow_has_committed_to() -> None:
    """A run parked at its gate has not decided what comes after, so "2 of 3"
    would be a guess about a step that does not exist yet."""
    d1, d2 = decision(at=0), decision(at=10)
    a = scheduled("1", by=d1, at=1, node_id="search")
    b = scheduled("2", by=d2, at=11, node_id="synthesize")

    graph = build([wf_started(), d1, a, started(a, at=1, identity="w"), completed(a, at=9), d2, b])

    assert (graph.completed, graph.total) == (1, 2)


def test_the_workflow_name_comes_from_history() -> None:
    graph = build([wf_started(name="durability_demo")])

    assert graph.workflow_name == "durability_demo"


def test_a_full_research_agent_run_reconstructs_end_to_end() -> None:
    """The shape the whole view exists to show: search → 3 summaries in parallel
    → synthesis → human gate → publish, with the fan-out on one layer."""
    d1, d2, d3, d4, d5 = (decision(at=x) for x in (0, 10, 40, 60, 200))
    search = scheduled("1", by=d1, at=1, node_id="search")
    sums = [scheduled(str(i + 2), by=d2, at=11, node_id=f"summarize-{i}") for i in range(3)]
    synth = scheduled("5", by=d3, at=41, node_id="synthesize")
    op = gate_open("publish", by=d4, at=61)
    cl = gate_close("publish", by=d5, at=201, decided_by="ritankar")
    pub = scheduled("8", by=d5, at=202, node_id="publish-report", node_type="http")

    events = [
        wf_started(),
        d1,
        search,
        started(search, at=1, identity="w"),
        completed(search, at=9),
        d2,
    ]
    for i, s in enumerate(sums):
        events += [s, started(s, at=11, identity="w"), completed(s, at=20 + i)]
    events += [
        d3,
        synth,
        started(synth, at=41, identity="w"),
        completed(synth, at=55),
        d4,
        op,
        completed(op, at=62),
        d5,
        cl,
        pub,
        started(pub, at=202, identity="w"),
        completed(pub, at=205),
    ]

    graph = build(events, status="Completed", terminal=True)

    assert [n.label for n in graph.nodes if n.layer == 1] == [
        "summarize-0",
        "summarize-1",
        "summarize-2",
    ]
    assert by_label(graph, "synthesize").layer == 2
    assert by_label(graph, "publish").kind == "gate"
    assert by_label(graph, "publish").layer == 3
    assert by_label(graph, "publish-report").layer == 4
    assert graph.total == 7
    assert graph.completed == 7
    # Fan-out then fan-in: three edges out of search, three into synthesize.
    synth_id = by_label(graph, "synthesize").id
    assert len([e for e in graph.edges if e.target == synth_id]) == 3
