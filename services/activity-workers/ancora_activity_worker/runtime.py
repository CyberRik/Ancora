"""Process-wide runtime context for activities (dependency-injection seams).

Activities need three collaborators that differ between production and tests:
  * the execution **backend** (Ray vs. local pool),
  * a Temporal **client** for async activity completion (Model B), and
  * a **node recorder** for the crude ``node_execution`` projection.

Rather than thread these through every activity signature, the worker installs
them here at startup; tests install lightweight fakes. Activities read them via
the getters, which fail loudly if the worker forgot to configure the backend.
"""

from __future__ import annotations

from typing import Any, Protocol

from temporalio.client import Client

from ancora_activity_worker.ray_bridge import Backend, LocalBackend

_backend: Backend | None = None
_completion_client: Client | None = None


def set_backend(backend: Backend) -> None:
    global _backend
    _backend = backend


def get_backend() -> Backend:
    if _backend is None:
        # A safe default so unit tests that forget to configure still work.
        set_backend(LocalBackend())
    assert _backend is not None
    return _backend


def set_completion_client(client: Client) -> None:
    """The client used to complete async (Model B) activities out-of-band."""
    global _completion_client
    _completion_client = client


def get_completion_client() -> Client:
    if _completion_client is None:
        raise RuntimeError(
            "async activity completion requires a Temporal client; "
            "call runtime.set_completion_client() at worker startup"
        )
    return _completion_client


class NodeRecorder(Protocol):
    async def record_start(self, meta: dict[str, Any]) -> None: ...
    async def record_finish(self, meta: dict[str, Any]) -> None: ...


class _NoopRecorder:
    async def record_start(self, meta: dict[str, Any]) -> None:
        return None

    async def record_finish(self, meta: dict[str, Any]) -> None:
        return None


_recorder: NodeRecorder = _NoopRecorder()


def set_node_recorder(recorder: NodeRecorder) -> None:
    global _recorder
    _recorder = recorder


def get_node_recorder() -> NodeRecorder:
    return _recorder


def reset() -> None:
    """Tear down process globals (used between tests)."""
    global _backend, _completion_client, _recorder
    _backend = None
    _completion_client = None
    _recorder = _NoopRecorder()
