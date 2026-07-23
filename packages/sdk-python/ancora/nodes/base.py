"""The node contract (AN-050).

A **node** is the unit of work in an Ancora workflow: a typed, self-describing
step with declared inputs/outputs, resource needs, a sandbox tier, and a cost
hook. Built-in nodes (LLM, HTTP, Database, Python, Approval) subclass :class:`Node`;
third-party nodes will too once the plugin contract lands (Phase 5).

Nodes contain the *side-effecting* logic — network calls, model inference, SQL.
They therefore run **inside activities**, never in workflow code, so Temporal can
retry and replay them safely. The :class:`NodeContext` handed to ``execute`` is
the node's only channel back to the runtime: it carries the idempotency key (so a
node can make its own effects exactly-once), a structured logger, and the cost
recorder (AN-056).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar, Protocol

from pydantic import BaseModel, Field


def _merge_label(left: str | None, right: str | None) -> str | None:
    """Combine two provenance labels when summing costs.

    An unset side carries no information, so it must not erase a set one — the
    accumulator starts at ``Cost()`` with everything ``None``, and treating that
    as a conflicting value would strip the provider and model off the very first
    recorded cost. Only two *different* real labels collapse to ``None``, which
    honestly reports "this total spans more than one source".
    """
    if left is None:
        return right
    if right is None or left == right:
        return left
    return None


class Cost(BaseModel):
    """Normalized cost of a single node execution (AN-056).

    Nodes report whichever dimensions apply; the workflow accumulates them for
    reporting and (Phase 3 soft / v2 hard) budget enforcement (AN-057).
    """

    usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    gpu_seconds: float = 0.0
    provider: str | None = None
    model: str | None = None

    def __add__(self, other: Cost) -> Cost:
        return Cost(
            usd=self.usd + other.usd,
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            gpu_seconds=self.gpu_seconds + other.gpu_seconds,
            provider=_merge_label(self.provider, other.provider),
            model=_merge_label(self.model, other.model),
        )


class ResourceHint(BaseModel):
    """What a node asks the scheduler/backend for (maps to ``ResourceSpec``)."""

    num_cpus: float = 1.0
    num_gpus: float = 0.0
    accelerator_type: str | None = None
    memory_mb: int | None = None


class Sandbox:
    """Isolation tiers (RFC-0005). MVP built-ins run at ``T0`` (in-process)."""

    T0 = "t0"  # in-process — trusted built-ins
    T1 = "t1"  # Ray runtime-env dependency isolation
    T2 = "t2"  # container isolation (v2)


class Logger(Protocol):
    """Structured-logging surface a node may use (satisfied by ``activity.logger``)."""

    def info(self, msg: str, *args: Any, **kwargs: Any) -> None: ...
    def warning(self, msg: str, *args: Any, **kwargs: Any) -> None: ...
    def error(self, msg: str, *args: Any, **kwargs: Any) -> None: ...


class _NullLogger:
    def info(self, msg: str, *args: Any, **kwargs: Any) -> None: ...
    def warning(self, msg: str, *args: Any, **kwargs: Any) -> None: ...
    def error(self, msg: str, *args: Any, **kwargs: Any) -> None: ...


class NodeContext:
    """Runtime handed to :meth:`Node.execute` — the node's channel to the runtime.

    * ``idempotency_key`` — stable across retries of the same logical call
      (derived in the SDK, AN-062), so a node can guard its own effects.
    * ``record_cost`` — accumulate cost for this execution; the activity returns
      it and the workflow rolls it up (AN-057).
    * ``log`` — structured logger (the activity passes Temporal's).
    * ``attempt`` — Temporal activity attempt number (1-based).
    """

    def __init__(
        self,
        *,
        node_id: str,
        idempotency_key: str,
        attempt: int = 1,
        log: Logger | None = None,
    ) -> None:
        self.node_id = node_id
        self.idempotency_key = idempotency_key
        self.attempt = attempt
        self.log: Logger = log or _NullLogger()
        self._cost = Cost()

    def record_cost(self, cost: Cost) -> None:
        self._cost = self._cost + cost

    @property
    def total_cost(self) -> Cost:
        return self._cost


class NodeSchema(BaseModel):
    """Discovery record for a node type (AN-058) — surfaced by ``GET /v1/plugins``."""

    type_name: str
    version: str
    summary: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    resources: ResourceHint
    sandbox: str
    idempotent: bool


class Node(ABC):
    """Base class for all Ancora nodes.

    Subclasses declare four class attributes and implement :meth:`execute`::

        class MyNode(Node):
            type_name = "my_node"
            version = "1.0.0"
            input_model = MyInput
            output_model = MyOutput

            async def execute(self, inp: MyInput, ctx: NodeContext) -> MyOutput: ...

    The I/O models are Pydantic classes; the runtime validates payloads against
    them at the activity boundary so a node never sees malformed input.
    """

    # Declared by every concrete node. Enforced at registration (AN-058).
    type_name: ClassVar[str] = ""
    version: ClassVar[str] = "1.0.0"
    summary: ClassVar[str] = ""
    input_model: ClassVar[type[BaseModel]]
    output_model: ClassVar[type[BaseModel]]
    resources: ClassVar[ResourceHint] = ResourceHint()
    sandbox: ClassVar[str] = Sandbox.T0
    # Whether repeating this node's effect is unsafe (→ guarded via the inbox).
    idempotent: ClassVar[bool] = True

    @abstractmethod
    async def execute(self, inp: Any, ctx: NodeContext) -> BaseModel:
        """Run the node's side-effecting logic and return a validated output."""
        raise NotImplementedError

    @classmethod
    def schema(cls) -> NodeSchema:
        cls._check_declared()
        return NodeSchema(
            type_name=cls.type_name,
            version=cls.version,
            summary=cls.summary or (cls.__doc__ or "").strip().split("\n")[0],
            input_schema=cls.input_model.model_json_schema(),
            output_schema=cls.output_model.model_json_schema(),
            resources=cls.resources,
            sandbox=cls.sandbox,
            idempotent=cls.idempotent,
        )

    @classmethod
    def _check_declared(cls) -> None:
        missing = [
            attr
            for attr in ("type_name", "input_model", "output_model")
            if not getattr(cls, attr, None)
        ]
        if missing:
            raise ValueError(f"{cls.__name__} is missing node declarations: {', '.join(missing)}")


class NodeError(Exception):
    """Raised by a node when execution fails.

    ``transient`` distinguishes retryable failures (timeouts, 5xx, rate limits)
    from terminal ones (bad input, 4xx) so the scheduler's retry policy can fail
    fast on the latter (AN-044).
    """

    def __init__(self, message: str, *, transient: bool = False, retry_after: float | None = None):
        super().__init__(message)
        self.transient = transient
        self.retry_after = retry_after


class RetryAfter(BaseModel):
    """Parsed ``Retry-After`` hint returned alongside a transient failure."""

    seconds: float = Field(ge=0)
