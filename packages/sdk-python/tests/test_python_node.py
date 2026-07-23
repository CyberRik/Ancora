"""PythonNode tests (AN-053).

The security property under test is the important one: workflow input names a
*registered* function, never an importable path. If that ever regressed, a
workflow's input would become a remote-code-execution primitive.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Iterator

import pytest

from ancora.nodes.base import NodeContext, NodeError
from ancora.nodes.python_node import (
    PythonInput,
    PythonNode,
    clear_functions,
    python_function,
    register_function,
    registered_functions,
)


def ctx() -> NodeContext:
    return NodeContext(node_id="py-1", idempotency_key="k")


# Module-scope functions so the subprocess path (which re-imports by name) can
# reach them. A closure or lambda deliberately cannot be run in isolation.
def add(a: int, b: int) -> int:
    return a + b


def boom() -> None:
    raise ValueError("nope")


def sleeper(seconds: float) -> str:
    time.sleep(seconds)
    return "done"


def hog() -> int:
    # Allocate well past any sane RLIMIT_AS the test sets.
    return len(bytearray(600 * 1024 * 1024))


@pytest.fixture(autouse=True)
def _registry() -> Iterator[None]:
    clear_functions()
    register_function("add", add)
    register_function("boom", boom)
    register_function("sleeper", sleeper)
    register_function("hog", hog)
    yield
    clear_functions()


async def run(**kwargs: object) -> object:
    return await PythonNode().execute(PythonInput(**kwargs), ctx())  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Registration is the security boundary
# --------------------------------------------------------------------------- #
async def test_input_cannot_name_an_arbitrary_import() -> None:
    # The canonical attack: "os:system". Nothing in the node resolves a dotted
    # path, so this is simply an unknown name.
    with pytest.raises(NodeError) as exc:
        await run(function="os:system", args=["echo pwned"])
    assert "not registered" in str(exc.value)
    assert not exc.value.transient


def test_the_decorator_registers_under_the_given_name() -> None:
    @python_function("decorated")
    def _fn() -> str:
        return "hi"

    assert "decorated" in registered_functions()


# --------------------------------------------------------------------------- #
# In-process execution (T0)
# --------------------------------------------------------------------------- #
async def test_runs_a_registered_function_in_process() -> None:
    out = await run(function="add", args=[2, 3])
    assert out.value == 5  # type: ignore[attr-defined]
    assert out.isolation == "inprocess"  # type: ignore[attr-defined]


async def test_keyword_arguments_are_passed_through() -> None:
    out = await run(function="add", kwargs={"a": 4, "b": 6})
    assert out.value == 10  # type: ignore[attr-defined]


async def test_a_raising_function_becomes_a_terminal_node_error() -> None:
    with pytest.raises(NodeError) as exc:
        await run(function="boom")
    assert "ValueError: nope" in str(exc.value)
    assert not exc.value.transient


async def test_the_timeout_is_enforced_in_process() -> None:
    with pytest.raises(NodeError, match="exceeded"):
        await run(function="sleeper", args=[5.0], timeout_seconds=0.2)


async def test_unknown_isolation_is_rejected() -> None:
    with pytest.raises(NodeError, match="unknown isolation"):
        await run(function="add", args=[1, 1], isolation="vm")


# --------------------------------------------------------------------------- #
# Subprocess isolation (T1)
# --------------------------------------------------------------------------- #
async def test_runs_in_a_child_interpreter() -> None:
    out = await run(function="add", args=[7, 8], isolation="subprocess")
    assert out.value == 15  # type: ignore[attr-defined]
    assert out.isolation == "subprocess"  # type: ignore[attr-defined]


async def test_a_child_exception_is_reported_not_swallowed() -> None:
    with pytest.raises(NodeError) as exc:
        await run(function="boom", isolation="subprocess")
    assert "ValueError: nope" in str(exc.value)


async def test_the_timeout_kills_the_child() -> None:
    with pytest.raises(NodeError, match="subprocess killed"):
        await run(function="sleeper", args=[30.0], isolation="subprocess", timeout_seconds=0.5)


async def test_a_closure_cannot_be_isolated_and_says_why() -> None:
    def local_fn() -> int:  # not importable by name from a child
        return 1

    register_function("local", local_fn)
    with pytest.raises(NodeError, match="not importable in a subprocess"):
        await run(function="local", isolation="subprocess")


@pytest.mark.skipif(
    sys.platform == "win32", reason="RLIMIT_AS is a POSIX facility; Windows has no equivalent"
)
async def test_exceeding_the_memory_limit_fails_cleanly() -> None:
    # The AC for AN-053: the limit is enforced and the failure is a clean,
    # terminal node error rather than the worker being taken down with it.
    with pytest.raises(NodeError) as exc:
        await run(function="hog", isolation="subprocess", memory_mb=64, timeout_seconds=30.0)
    assert not exc.value.transient
    assert "memory" in str(exc.value).lower() or "died in isolation" in str(exc.value)


# --------------------------------------------------------------------------- #
# Contract
# --------------------------------------------------------------------------- #
def test_the_node_is_guarded_by_the_inbox() -> None:
    # User code may do anything, including a non-repeatable side effect.
    assert PythonNode.idempotent is False
