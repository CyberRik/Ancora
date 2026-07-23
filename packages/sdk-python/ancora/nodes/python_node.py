"""PythonNode — run a registered Python callable under declared limits (AN-053).

This is the escape hatch: arbitrary user compute, expressed as a normal Python
function, executed durably like any other node. Two properties make that safe
enough to ship.

**No arbitrary import.** The node never imports a module named in its input.
A workflow's input is data, and data that can name any importable symbol is a
remote-code-execution primitive. Instead, functions are published to an explicit
allow-list (:func:`register_function`, or the ``@python_function`` decorator) by
the worker's own code at startup; input may only *reference a registered name*.

**Declared limits, enforced.** ``isolation="subprocess"`` (Sandbox T1) runs the
call in a child interpreter with a wall-clock timeout and, on POSIX, an address
-space cap from the node's ``memory_mb``. Exceeding either kills the child and
surfaces a clean terminal :class:`NodeError` instead of taking the worker down
with it — the AC for this issue. In-process (T0) execution stays available for
trusted, cheap functions and still enforces the timeout.

A function that OOMs or blows its timeout fails *terminally*: re-running it would
consume the same resources and fail identically, so retrying is pure waste.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from collections.abc import Callable
from typing import Any, Final

from pydantic import BaseModel, Field

from ancora.nodes.base import Node, NodeContext, NodeError, ResourceHint, Sandbox
from ancora.nodes.registry import register

# name → callable. Populated by worker startup code, never by workflow input.
_FUNCTIONS: dict[str, Callable[..., Any]] = {}

# The child-process bootstrap. Reads a JSON job on stdin, writes a JSON result on
# stdout. Kept as a string (not a module) so it runs from any working directory
# and needs nothing on the child's path beyond what the parent already imports.
_CHILD_BOOTSTRAP: Final = """
import importlib, json, sys

job = json.loads(sys.stdin.read())

limit_mb = job.get("memory_mb")
if limit_mb:
    try:
        import resource
        nbytes = int(limit_mb) * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (nbytes, nbytes))
    except Exception:
        pass  # not available on this platform; the parent still enforces timeout

mod = importlib.import_module(job["module"])
fn = getattr(mod, job["qualname"])
try:
    value = fn(*job["args"], **job["kwargs"])
except MemoryError:
    print(json.dumps({"error": "memory limit exceeded", "kind": "memory"}))
    sys.exit(0)
except Exception as exc:
    print(json.dumps({"error": f"{type(exc).__name__}: {exc}", "kind": "raised"}))
    sys.exit(0)
print(json.dumps({"value": value}))
"""


def register_function(name: str, fn: Callable[..., Any]) -> None:
    """Publish ``fn`` under ``name`` so a workflow may reference it by name."""
    _FUNCTIONS[name] = fn


def python_function(name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator form of :func:`register_function`."""

    def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
        register_function(name, fn)
        return fn

    return decorate


def registered_functions() -> dict[str, Callable[..., Any]]:
    return dict(_FUNCTIONS)


def clear_functions() -> None:
    _FUNCTIONS.clear()


class PythonInput(BaseModel):
    """Which registered function to run, with what arguments and limits."""

    function: str = Field(description="Name registered via register_function/@python_function.")
    args: list[Any] = Field(default_factory=list)
    kwargs: dict[str, Any] = Field(default_factory=dict)
    # "inprocess" (T0, trusted+cheap) or "subprocess" (T1, isolated+limited).
    isolation: str = "inprocess"
    timeout_seconds: float = 300.0
    # Address-space ceiling for the child. Only enforceable on POSIX; ignored
    # elsewhere (the timeout still bounds a runaway).
    memory_mb: int | None = None


class PythonOutput(BaseModel):
    value: Any = None
    isolation: str = "inprocess"


def _resolve(name: str) -> Callable[..., Any]:
    fn = _FUNCTIONS.get(name)
    if fn is None:
        known = ", ".join(sorted(_FUNCTIONS)) or "<none registered>"
        raise NodeError(
            f"python function {name!r} is not registered (available: {known})",
            transient=False,
        )
    return fn


async def _run_inprocess(fn: Callable[..., Any], inp: PythonInput) -> Any:
    """Call ``fn`` off the event loop so a CPU-bound function cannot block it."""

    def call() -> Any:
        return fn(*inp.args, **inp.kwargs)

    try:
        return await asyncio.wait_for(asyncio.to_thread(call), timeout=inp.timeout_seconds)
    except TimeoutError as exc:
        raise NodeError(
            f"python function {inp.function!r} exceeded {inp.timeout_seconds}s", transient=False
        ) from exc
    except MemoryError as exc:
        raise NodeError(
            f"python function {inp.function!r} ran out of memory", transient=False
        ) from exc
    except Exception as exc:
        raise NodeError(f"{type(exc).__name__}: {exc}", transient=False) from exc


async def _run_subprocess(fn: Callable[..., Any], inp: PythonInput) -> Any:
    """Run ``fn`` in a child interpreter with a memory cap and a hard timeout."""
    module = getattr(fn, "__module__", None)
    qualname = getattr(fn, "__qualname__", None)
    if not module or not qualname or "." in qualname or module == "__main__":
        # The child re-imports by name; closures, lambdas, and __main__-defined
        # functions cannot be reached that way. Say so rather than failing opaquely.
        raise NodeError(
            f"python function {inp.function!r} is not importable in a subprocess "
            "(define it at module scope in an importable module, or use isolation='inprocess')",
            transient=False,
        )

    job = json.dumps(
        {
            "module": module,
            "qualname": qualname,
            "args": inp.args,
            "kwargs": inp.kwargs,
            "memory_mb": inp.memory_mb,
        }
    )

    # The child is a bare interpreter: it inherits the environment but not the
    # parent's ``sys.path``. Without this, a function defined in the worker's own
    # package is unreachable from isolation — which would make T1 useless for
    # exactly the code people want to isolate.
    child_path = os.pathsep.join(p for p in sys.path if p)

    def spawn() -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 — fixed argv; the payload is stdin, not shell
            [sys.executable, "-c", _CHILD_BOOTSTRAP],
            input=job,
            capture_output=True,
            text=True,
            timeout=inp.timeout_seconds,
            env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONPATH": child_path},
        )

    try:
        proc = await asyncio.to_thread(spawn)
    except subprocess.TimeoutExpired as exc:
        raise NodeError(
            f"python function {inp.function!r} exceeded {inp.timeout_seconds}s (subprocess killed)",
            transient=False,
        ) from exc

    if proc.returncode != 0:
        # A non-zero exit without a JSON envelope means the child died outright —
        # the OOM killer, a segfault, or an RLIMIT_AS abort.
        raise NodeError(
            f"python function {inp.function!r} died in isolation "
            f"(exit {proc.returncode}): {proc.stderr.strip()[-500:] or 'no output'}",
            transient=False,
        )

    try:
        envelope = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise NodeError(
            f"python function {inp.function!r} produced unreadable output: "
            f"{proc.stdout.strip()[-500:]!r}",
            transient=False,
        ) from exc

    if "error" in envelope:
        raise NodeError(f"{envelope['error']}", transient=False)
    return envelope.get("value")


@register
class PythonNode(Node):
    """Run a registered Python callable, optionally in a resource-limited child."""

    type_name = "python"
    version = "1.0.0"
    summary = "Execute a registered Python function with declared resources and isolation."
    input_model = PythonInput
    output_model = PythonOutput
    resources = ResourceHint(num_cpus=1.0)
    sandbox = Sandbox.T1
    # User code may do anything, including a non-repeatable side effect. Guard it
    # through the inbox so a retry returns the first result rather than re-running.
    idempotent = False

    async def execute(self, inp: PythonInput, ctx: NodeContext) -> PythonOutput:
        fn = _resolve(inp.function)
        if inp.isolation not in ("inprocess", "subprocess"):
            raise NodeError(
                f"unknown isolation {inp.isolation!r} (expected 'inprocess' or 'subprocess')",
                transient=False,
            )
        ctx.log.info("running python node %s (%s)", inp.function, inp.isolation)
        if inp.isolation == "subprocess":
            value = await _run_subprocess(fn, inp)
        else:
            value = await _run_inprocess(fn, inp)
        return PythonOutput(value=value, isolation=inp.isolation)
