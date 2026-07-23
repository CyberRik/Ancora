"""Deterministic policy resolution (AN-039, AN-044, AN-046).

The property that matters most is that :func:`resolve_policy` is *pure*: the same
inputs must produce the same policy forever, because a workflow resolves it
during replay too. A policy that depended on a clock, an environment variable, or
a config file would make an old run's activity timeouts differ from the ones its
history recorded — the classic non-determinism failure.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from ancora.policy import (
    PRIORITY_BULK,
    PRIORITY_HIGH,
    PRIORITY_NORMAL,
    RetrySpec,
    resolve_policy,
    retry_policy_for,
)
from ancora_common.resources import Capability, queue_for


# --------------------------------------------------------------------------- #
# Purity — the replay-safety requirement
# --------------------------------------------------------------------------- #
def test_resolution_is_stable_across_calls() -> None:
    a = resolve_policy("llm", {"priority": "high"})
    b = resolve_policy("llm", {"priority": "high"})
    assert a == b


def test_resolution_does_not_mutate_the_override_mapping() -> None:
    overrides = {"priority": "bulk", "max_attempts": 9}
    snapshot = dict(overrides)
    resolve_policy("http", overrides)
    assert overrides == snapshot


# --------------------------------------------------------------------------- #
# Routing (AN-033, AN-043)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("type_name", "capability"),
    [
        ("llm", Capability.CPU),
        ("http", Capability.IO),
        ("database", Capability.IO),
        ("python", Capability.CPU),
    ],
)
def test_node_classes_route_to_their_capability_queue(
    type_name: str, capability: Capability
) -> None:
    assert resolve_policy(type_name).task_queue == queue_for(capability)


def test_an_explicit_queue_overrides_the_capability_mapping() -> None:
    assert resolve_policy("llm", {"task_queue": "ancora-gpu"}).task_queue == "ancora-gpu"


def test_capability_override_reroutes_the_node() -> None:
    assert resolve_policy("llm", {"capability": "gpu"}).task_queue == queue_for(Capability.GPU)


def test_priority_lanes_accept_names_and_numbers() -> None:
    assert resolve_policy("llm").priority == PRIORITY_NORMAL
    assert resolve_policy("llm", {"priority": "high"}).priority == PRIORITY_HIGH
    assert resolve_policy("llm", {"priority": "bulk"}).priority == PRIORITY_BULK
    assert resolve_policy("llm", {"priority": 2}).priority == 2


def test_an_unknown_priority_name_is_a_clear_error() -> None:
    with pytest.raises(ValueError, match="unknown priority"):
        resolve_policy("llm", {"priority": "urgent-ish"})


# --------------------------------------------------------------------------- #
# Retry shapes per node class (AN-044)
# --------------------------------------------------------------------------- #
def test_llm_retries_more_patiently_than_http() -> None:
    llm = resolve_policy("llm").retry
    http = resolve_policy("http").retry
    # An LLM chain is expensive to lose and fails transiently; a broken endpoint
    # should surface to the workflow rather than be hammered.
    assert llm.maximum_attempts > http.maximum_attempts
    assert llm.maximum_seconds > http.maximum_seconds


def test_python_code_is_assumed_buggy_not_flaky() -> None:
    assert resolve_policy("python").retry.maximum_attempts == 2


def test_an_approval_gate_dispatched_as_an_activity_fails_fast() -> None:
    # Dispatching a gate as an activity is a bug; retrying it five times just
    # delays the error.
    assert resolve_policy("approval").retry.maximum_attempts == 1


def test_retry_fields_are_individually_overridable() -> None:
    policy = resolve_policy("http", {"max_attempts": 9, "retry_initial_seconds": 5.0})
    assert policy.retry.maximum_attempts == 9
    assert policy.retry.initial_seconds == 5.0
    # Untouched fields keep the class default.
    assert policy.retry.backoff_coefficient == resolve_policy("http").retry.backoff_coefficient


def test_retry_spec_converts_to_a_temporal_policy() -> None:
    temporal = RetrySpec(
        initial_seconds=2.0,
        backoff_coefficient=3.0,
        maximum_seconds=30.0,
        maximum_attempts=4,
        non_retryable=("Fatal",),
    ).to_temporal()
    assert temporal.initial_interval == timedelta(seconds=2)
    assert temporal.backoff_coefficient == 3.0
    assert temporal.maximum_attempts == 4
    assert temporal.non_retryable_error_types == ["Fatal"]


def test_retry_policy_for_exposes_the_class_default() -> None:
    assert retry_policy_for("llm").maximum_attempts == 6


# --------------------------------------------------------------------------- #
# Unknown node types
# --------------------------------------------------------------------------- #
def test_an_unregistered_type_gets_a_conservative_generic_policy() -> None:
    # A third-party node must be dispatchable before it has a table entry.
    policy = resolve_policy("some-future-plugin")
    assert policy.task_queue == queue_for(Capability.CPU)
    assert policy.retry.maximum_attempts == 3


# --------------------------------------------------------------------------- #
# Deadlines (AN-046)
# --------------------------------------------------------------------------- #
def test_no_deadline_means_no_schedule_to_close() -> None:
    assert resolve_policy("llm").schedule_to_close is None


def test_a_deadline_becomes_the_schedule_to_close_timeout() -> None:
    policy = resolve_policy("llm", deadline_remaining=timedelta(seconds=45))
    assert policy.schedule_to_close == timedelta(seconds=45)


def test_a_deadline_shorter_than_the_default_clamps_the_attempt_timeout() -> None:
    # The class default for llm is 300s; a 30s deadline must win.
    policy = resolve_policy("llm", deadline_remaining=timedelta(seconds=30))
    assert policy.start_to_close == timedelta(seconds=30)


def test_a_deadline_longer_than_the_default_does_not_extend_the_attempt() -> None:
    policy = resolve_policy("http", deadline_remaining=timedelta(hours=1))
    assert policy.start_to_close == resolve_policy("http").start_to_close


def test_an_already_passed_deadline_produces_a_zero_window() -> None:
    policy = resolve_policy("http", deadline_remaining=timedelta(seconds=-5))
    assert policy.schedule_to_close == timedelta(0)


# --------------------------------------------------------------------------- #
# Heartbeats
# --------------------------------------------------------------------------- #
def test_long_running_python_nodes_heartbeat_so_they_can_be_cancelled() -> None:
    assert resolve_policy("python").heartbeat == timedelta(seconds=30)
    assert resolve_policy("http").heartbeat is None
