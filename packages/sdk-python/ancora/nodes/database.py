"""DatabaseNode — parameterized SQL against a named, pooled datasource (AN-054).

Three rules make this node safe to hand a workflow's input:

**Parameters only, never interpolation.** The SQL text is bound with SQLAlchemy
``text()`` and a parameter mapping, so values travel to the driver out-of-band.
There is no code path that concatenates input into SQL — injection is not
"filtered", it is structurally impossible.

**Named datasources, not connection strings.** A workflow references a
datasource by name; the URL (and its credentials) is registered by the worker at
startup. Workflow input therefore cannot point the node at an arbitrary host, and
credentials never enter Temporal history.

**Read/write split, enforced.** A datasource may declare a separate read URL, and
``mode="read"`` refuses to run a statement that mutates. A datasource marked
``readonly`` refuses writes outright.

Engines are created once per datasource and cached, so the connection pool is
shared across every activity execution in the worker rather than rebuilt per call.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from pydantic import BaseModel, Field

from ancora.nodes.base import Node, NodeContext, NodeError, ResourceHint
from ancora.nodes.registry import register

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.ext.asyncio import AsyncEngine

# Statements that change data. Matched at the start of the (comment-stripped)
# statement, so `mode="read"` cannot be talked into running a DELETE.
_MUTATING: Final = re.compile(
    r"^\s*(insert|update|delete|merge|truncate|drop|alter|create|grant|revoke|call|do)\b",
    re.IGNORECASE,
)
_COMMENTS: Final = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)
_STRINGS: Final = re.compile(r"'(?:''|[^'])*'")


@dataclass(frozen=True)
class Datasource:
    """A named database target. ``read_url`` enables the read/write split."""

    name: str
    url: str
    read_url: str | None = None
    readonly: bool = False
    pool_size: int = 5
    max_overflow: int = 5
    pool_recycle_seconds: int = 1800


_DATASOURCES: dict[str, Datasource] = {}
_ENGINES: dict[tuple[str, str], AsyncEngine] = {}


def register_datasource(ds: Datasource) -> None:
    """Publish a datasource so workflows may reference it by name."""
    _DATASOURCES[ds.name] = ds


def clear_datasources() -> None:
    _DATASOURCES.clear()
    _ENGINES.clear()


async def dispose_engines() -> None:
    """Close every pooled engine (worker shutdown / test teardown)."""
    for engine in list(_ENGINES.values()):
        await engine.dispose()
    _ENGINES.clear()


def _engine_for(ds: Datasource, mode: str) -> AsyncEngine:
    """Lazily build (and cache) the pooled engine for this datasource + mode."""
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import QueuePool

    url = ds.read_url if (mode == "read" and ds.read_url) else ds.url
    cache_key = (ds.name, url)
    engine = _ENGINES.get(cache_key)
    if engine is None:
        options: dict[str, Any] = {
            "pool_recycle": ds.pool_recycle_seconds,
            "pool_pre_ping": True,
        }
        # Sizing knobs belong to the queue-pool family only. SQLite and other
        # single-connection dialects default to StaticPool/NullPool and reject
        # them outright, so ask the dialect what pool it will actually use rather
        # than assuming every database pools the same way.
        parsed = make_url(url)
        # get_pool_class lives on DefaultDialect, not the narrower Dialect protocol.
        dialect: Any = parsed.get_dialect()
        pool_cls = dialect.get_pool_class(parsed)
        if issubclass(pool_cls, QueuePool):
            options["pool_size"] = ds.pool_size
            options["max_overflow"] = ds.max_overflow
        engine = create_async_engine(url, **options)
        _ENGINES[cache_key] = engine
    return engine


def _strip_literals(sql: str) -> str:
    """Remove comments and string literals so structural checks see only syntax."""
    return _STRINGS.sub("''", _COMMENTS.sub(" ", sql))


def is_mutating(sql: str) -> bool:
    return bool(_MUTATING.match(_strip_literals(sql)))


def assert_single_statement(sql: str) -> None:
    """Reject stacked statements (``...; DROP TABLE x``) before they reach the driver."""
    body = _strip_literals(sql).strip().rstrip(";")
    if ";" in body:
        raise NodeError(
            "sql must be a single statement; stacked statements are rejected",
            transient=False,
        )


class DatabaseInput(BaseModel):
    datasource: str = Field(description="Name registered via register_datasource.")
    sql: str = Field(description="A single parameterized statement (:name placeholders).")
    params: dict[str, Any] = Field(default_factory=dict)
    # "read" routes to the datasource's read replica (when configured) and
    # refuses mutating statements; "write" always uses the primary.
    mode: str = "read"
    # "all" | "one" | "none" — how much of the result set to materialize.
    fetch: str = "all"
    # Safety valve so a runaway SELECT cannot pull an unbounded result into memory.
    max_rows: int = 1000


class DatabaseOutput(BaseModel):
    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    # True when the result set was cut off at ``max_rows``.
    truncated: bool = False
    mode: str = "read"


# SQLSTATE classes that describe a condition the *same query* can survive: the
# server was momentarily unable to serve it, not unwilling to. Everything else —
# syntax, missing relation, constraint violation — fails identically on retry.
_RETRYABLE_SQLSTATES: Final = frozenset(
    {
        "40001",  # serialization_failure
        "40P01",  # deadlock_detected
        "53300",  # too_many_connections
        "55P03",  # lock_not_available
        "57P03",  # cannot_connect_now
        "08000",  # connection_exception
        "08003",  # connection_does_not_exist
        "08006",  # connection_failure
    }
)


def _sqlstate(exc: Exception) -> str | None:
    """Best-effort SQLSTATE from whichever driver raised. Drivers disagree."""
    orig = getattr(exc, "orig", None)
    for attr in ("sqlstate", "pgcode"):
        code = getattr(orig, attr, None)
        if code:
            return str(code)
    return None


def _classify(exc: Exception) -> NodeError:
    """Decide whether retrying this failure could plausibly succeed.

    Getting this wrong is expensive in both directions: retrying a syntax error
    burns the node's whole attempt budget for nothing, while giving up on a
    deadlock throws away a run over a condition that clears in milliseconds.

    ``OperationalError`` deliberately does *not* imply transient. Drivers use it
    for anything the database refused at runtime, which includes "no such table"
    — permanent — as well as "connection lost" — not. The reliable signals are
    SQLAlchemy's ``connection_invalidated`` flag and the SQLSTATE class, so those
    decide rather than the exception type.
    """
    from sqlalchemy.exc import (
        DataError,
        DBAPIError,
        DisconnectionError,
        IntegrityError,
        ProgrammingError,
    )

    if isinstance(exc, ProgrammingError | IntegrityError | DataError):
        return NodeError(f"{type(exc).__name__}: {exc}", transient=False)
    if isinstance(exc, DisconnectionError):
        return NodeError(f"{type(exc).__name__}: {exc}", transient=True)
    if isinstance(exc, DBAPIError):
        transient = bool(exc.connection_invalidated) or (_sqlstate(exc) in _RETRYABLE_SQLSTATES)
        return NodeError(f"{type(exc).__name__}: {exc}", transient=transient)
    return NodeError(f"{type(exc).__name__}: {exc}", transient=False)


@register
class DatabaseNode(Node):
    """Execute one parameterized statement against a registered datasource."""

    type_name = "database"
    version = "1.0.0"
    summary = "Run a parameterized SQL statement against a named, pooled datasource."
    input_model = DatabaseInput
    output_model = DatabaseOutput
    resources = ResourceHint(num_cpus=0.5)
    # Reads are safe to repeat; writes are not, and the runtime cannot tell which
    # a given call is until it inspects the SQL. Declaring the node non-idempotent
    # routes every execution through the inbox, so a retried write fires once.
    idempotent = False

    async def execute(self, inp: DatabaseInput, ctx: NodeContext) -> DatabaseOutput:
        from sqlalchemy import text

        ds = _DATASOURCES.get(inp.datasource)
        if ds is None:
            known = ", ".join(sorted(_DATASOURCES)) or "<none registered>"
            raise NodeError(
                f"datasource {inp.datasource!r} is not registered (available: {known})",
                transient=False,
            )
        if inp.mode not in ("read", "write"):
            raise NodeError(
                f"unknown mode {inp.mode!r} (expected 'read' or 'write')", transient=False
            )

        assert_single_statement(inp.sql)
        mutating = is_mutating(inp.sql)
        if mutating and inp.mode == "read":
            raise NodeError(
                "mutating statement rejected in read mode; set mode='write'", transient=False
            )
        if mutating and ds.readonly:
            raise NodeError(f"datasource {ds.name!r} is read-only", transient=False)

        engine = _engine_for(ds, inp.mode)
        ctx.log.info("database node %s on %s (%s)", ctx.node_id, ds.name, inp.mode)

        try:
            async with engine.begin() as conn:
                result = await conn.execute(text(inp.sql), inp.params)
                if inp.fetch == "none" or not result.returns_rows:
                    return DatabaseOutput(rows=[], row_count=result.rowcount, mode=inp.mode)
                if inp.fetch == "one":
                    row = result.mappings().first()
                    rows = [dict(row)] if row is not None else []
                    return DatabaseOutput(rows=rows, row_count=len(rows), mode=inp.mode)
                # Fetch one past the cap so truncation is detectable, not guessed.
                fetched = result.mappings().fetchmany(inp.max_rows + 1)
                truncated = len(fetched) > inp.max_rows
                rows = [dict(r) for r in fetched[: inp.max_rows]]
                return DatabaseOutput(
                    rows=rows, row_count=len(rows), truncated=truncated, mode=inp.mode
                )
        except NodeError:
            raise
        except Exception as exc:
            raise _classify(exc) from exc
