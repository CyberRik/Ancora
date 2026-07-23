"""DatabaseNode tests (AN-054).

Runs against a real in-process SQLite engine rather than a mock, because the
claim under test is about what reaches the *driver*: that values travel as bound
parameters and never as SQL text. A mock would happily agree with a broken
implementation.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from ancora.nodes.base import NodeContext, NodeError
from ancora.nodes.database import (
    DatabaseInput,
    DatabaseNode,
    Datasource,
    _classify,
    assert_single_statement,
    clear_datasources,
    dispose_engines,
    is_mutating,
    register_datasource,
)

DS = "test"


def ctx() -> NodeContext:
    return NodeContext(node_id="db-1", idempotency_key="k")


async def run(**kwargs: object) -> object:
    return await DatabaseNode().execute(DatabaseInput(datasource=DS, **kwargs), ctx())  # type: ignore[arg-type]


@pytest_asyncio.fixture(autouse=True)
async def _db() -> AsyncIterator[None]:
    clear_datasources()
    # One shared in-memory database for the whole test, via a named URI.
    register_datasource(
        Datasource(name=DS, url="sqlite+aiosqlite:///file:memdb?mode=memory&cache=shared&uri=true")
    )
    await run(sql="CREATE TABLE users (id INTEGER, name TEXT)", mode="write", fetch="none")
    await run(
        sql="INSERT INTO users (id, name) VALUES (:id, :name)",
        params={"id": 1, "name": "ada"},
        mode="write",
        fetch="none",
    )
    await run(
        sql="INSERT INTO users (id, name) VALUES (:id, :name)",
        params={"id": 2, "name": "grace"},
        mode="write",
        fetch="none",
    )
    yield
    await run(sql="DROP TABLE users", mode="write", fetch="none")
    await dispose_engines()
    clear_datasources()


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #
async def test_parameterized_select_returns_rows() -> None:
    out = await run(sql="SELECT id, name FROM users ORDER BY id", fetch="all")
    assert out.row_count == 2  # type: ignore[attr-defined]
    assert out.rows[0]["name"] == "ada"  # type: ignore[attr-defined]


async def test_fetch_one_returns_a_single_row() -> None:
    out = await run(sql="SELECT name FROM users WHERE id = :id", params={"id": 2}, fetch="one")
    assert out.rows == [{"name": "grace"}]  # type: ignore[attr-defined]


async def test_max_rows_truncates_and_says_so() -> None:
    out = await run(sql="SELECT id FROM users ORDER BY id", max_rows=1)
    assert out.row_count == 1  # type: ignore[attr-defined]
    assert out.truncated is True  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- #
# Injection is structurally impossible (the AC for AN-054)
# --------------------------------------------------------------------------- #
async def test_a_classic_injection_payload_is_treated_as_data() -> None:
    # If this value were interpolated, the table would be gone. As a bound
    # parameter it is just a string that matches nothing.
    out = await run(
        sql="SELECT id FROM users WHERE name = :name",
        params={"name": "ada'; DROP TABLE users; --"},
    )
    assert out.rows == []  # type: ignore[attr-defined]
    still_there = await run(sql="SELECT COUNT(*) AS n FROM users", fetch="one")
    assert still_there.rows[0]["n"] == 2  # type: ignore[attr-defined]


def test_stacked_statements_are_rejected_before_the_driver() -> None:
    with pytest.raises(NodeError, match="single statement"):
        assert_single_statement("SELECT 1; DROP TABLE users")


def test_a_trailing_semicolon_is_not_a_stacked_statement() -> None:
    assert_single_statement("SELECT 1;")


def test_a_semicolon_inside_a_literal_is_not_a_statement_break() -> None:
    assert_single_statement("SELECT * FROM t WHERE note = 'a; b'")


def test_a_semicolon_inside_a_comment_is_not_a_statement_break() -> None:
    assert_single_statement("SELECT 1 -- ; not a statement")


# --------------------------------------------------------------------------- #
# Read/write split
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM users",
        "  update users set name='x'",
        "INSERT INTO users VALUES (3, 'x')",
        "/* comment */ DROP TABLE users",
    ],
)
def test_mutating_statements_are_detected(sql: str) -> None:
    assert is_mutating(sql)


def test_selects_are_not_mutating() -> None:
    assert not is_mutating("SELECT * FROM users WHERE name = 'delete from x'")


async def test_read_mode_refuses_a_mutating_statement() -> None:
    with pytest.raises(NodeError, match="read mode"):
        await run(sql="DELETE FROM users", mode="read")


async def test_readonly_datasource_refuses_writes() -> None:
    register_datasource(
        Datasource(
            name="ro",
            url="sqlite+aiosqlite:///file:memdb?mode=memory&cache=shared&uri=true",
            readonly=True,
        )
    )
    node = DatabaseNode()
    with pytest.raises(NodeError, match="read-only"):
        await node.execute(
            DatabaseInput(datasource="ro", sql="DELETE FROM users", mode="write"), ctx()
        )


# --------------------------------------------------------------------------- #
# Guardrails
# --------------------------------------------------------------------------- #
async def test_unregistered_datasource_is_a_terminal_error() -> None:
    node = DatabaseNode()
    with pytest.raises(NodeError) as exc:
        await node.execute(DatabaseInput(datasource="nope", sql="SELECT 1"), ctx())
    assert not exc.value.transient  # naming a missing datasource will never succeed
    assert "not registered" in str(exc.value)


async def test_unknown_mode_is_rejected() -> None:
    with pytest.raises(NodeError, match="unknown mode"):
        await run(sql="SELECT 1", mode="sideways")


async def test_a_missing_table_is_terminal_not_transient() -> None:
    with pytest.raises(NodeError) as exc:
        await run(sql="SELECT * FROM no_such_table")
    # SQLite reports this as OperationalError, which is *not* on its own a reason
    # to retry — the table will still be missing on attempt five.
    assert not exc.value.transient


def test_a_serialization_failure_is_classified_transient() -> None:
    from sqlalchemy.exc import OperationalError

    class Orig(Exception):
        sqlstate = "40001"  # serialization_failure — clears on retry

    err = _classify(OperationalError("SELECT 1", {}, Orig()))
    assert err.transient


def test_an_undefined_column_is_classified_terminal() -> None:
    from sqlalchemy.exc import ProgrammingError

    err = _classify(ProgrammingError("SELECT nope", {}, Exception("undefined column")))
    assert not err.transient


def test_the_node_is_guarded_by_the_inbox() -> None:
    # The runtime cannot know whether a given call mutates until it inspects the
    # SQL, so every execution routes through the exactly-once guard.
    assert DatabaseNode.idempotent is False
