"""Execution backends: Ray in production, an in-process pool for dev/CI (AN-025/034).

An activity never calls Ray directly. It submits a *unit of compute* to a
:class:`Backend`, which returns a :class:`TaskHandle` the driver can poll, read
progress from, cancel, and collect a result from. Two backends implement it:

* :class:`RayBackend` — ``ray.remote`` with the node's resource request; cancel
  is ``ray.cancel``. Chosen when Ray is importable *and* an address is configured.
* :class:`LocalBackend` — a thread pool. Cooperative cancel via a shared
  ``threading.Event``; live progress via shared memory. This is what unit tests
  exercise, and what runs when no Ray cluster is present.

The compute function contract is identical for both: a callable taking a single
:class:`LiveProgress`. Bind everything else with ``functools.partial`` before
submit so the payload stays picklable for Ray.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any, Protocol

from ancora_common.resources import ResourceSpec

logger = logging.getLogger("ancora.runtime.bridge")

# A compute function receives progress and returns a JSON-able result.
ComputeFn = Callable[["LiveProgress"], Any]


class LiveProgress:
    """Thread-safe progress + cancel channel shared with an in-process compute.

    A compute function calls :meth:`report` at each checkpoint and checks
    :meth:`cancelled` cooperatively. The driver reads :meth:`latest` to emit
    Temporal heartbeats. Across a process boundary (Ray) this object does not
    travel; the Ray compute gets a fresh, inert instance and cancellation is
    delivered by ``ray.cancel`` instead.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._latest: dict[str, Any] | None = None
        self._cancel = threading.Event()

    def report(self, checkpoint: dict[str, Any]) -> None:
        with self._lock:
            self._latest = dict(checkpoint)

    def latest(self) -> dict[str, Any] | None:
        with self._lock:
            return self._latest

    def request_cancel(self) -> None:
        self._cancel.set()

    def cancelled(self) -> bool:
        return self._cancel.is_set()


class TaskHandle(Protocol):
    """A submitted unit of compute."""

    task_id: str | None

    def done(self) -> bool: ...
    def cancel(self) -> None: ...
    def result(self) -> Any:
        """Block for the result (call from a thread, not the event loop)."""
        ...


class Backend(Protocol):
    name: str

    def submit(
        self, fn: ComputeFn, *, resources: ResourceSpec, progress: LiveProgress
    ) -> TaskHandle: ...

    def shutdown(self) -> None: ...


# --------------------------------------------------------------------------- #
# Local (thread-pool) backend
# --------------------------------------------------------------------------- #
class _LocalTaskHandle:
    task_id: str | None = None

    def __init__(self, future: Future[Any], progress: LiveProgress) -> None:
        self._future = future
        self._progress = progress

    def done(self) -> bool:
        return self._future.done()

    def cancel(self) -> None:
        # Threads can't be force-killed: request cooperative stop, and cancel the
        # future in case it hasn't started running yet.
        self._progress.request_cancel()
        self._future.cancel()

    def result(self) -> Any:
        return self._future.result()


class LocalBackend:
    name = "local"

    def __init__(self, max_workers: int = 8) -> None:
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="ancora-compute"
        )

    def submit(
        self, fn: ComputeFn, *, resources: ResourceSpec, progress: LiveProgress
    ) -> TaskHandle:
        # ``resources`` is advisory here — the local pool has no accounting.
        future = self._pool.submit(fn, progress)
        return _LocalTaskHandle(future, progress)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


# --------------------------------------------------------------------------- #
# Ray backend
# --------------------------------------------------------------------------- #
class _RayTaskHandle:
    def __init__(self, ref: Any, ray_mod: Any) -> None:
        self._ref = ref
        self._ray = ray_mod
        try:
            self.task_id: str | None = ref.task_id().hex()
        except Exception:  # noqa: BLE001 — id is best-effort telemetry
            self.task_id = None

    def done(self) -> bool:
        ready, _ = self._ray.wait([self._ref], num_returns=1, timeout=0)
        return bool(ready)

    def cancel(self) -> None:
        # Non-force first (lets the task observe cancellation and release GPUs).
        try:
            self._ray.cancel(self._ref, force=False)
        except Exception as exc:  # noqa: BLE001
            logger.warning("ray.cancel failed: %s", exc)

    def result(self) -> Any:
        return self._ray.get(self._ref)


def _ray_entry(fn: ComputeFn) -> Any:
    """Runs inside the Ray worker: a fresh, process-local progress channel."""
    return fn(LiveProgress())


class RayBackend:
    name = "ray"

    def __init__(self, ray_mod: Any) -> None:
        self._ray = ray_mod

    def submit(
        self, fn: ComputeFn, *, resources: ResourceSpec, progress: LiveProgress
    ) -> TaskHandle:
        remote = self._ray.remote(**resources.to_ray_options())(_ray_entry)
        ref = remote.remote(fn)
        return _RayTaskHandle(ref, self._ray)

    def shutdown(self) -> None:
        # We don't own the cluster lifecycle here; main.py calls ray.shutdown().
        return None


# --------------------------------------------------------------------------- #
# Selection
# --------------------------------------------------------------------------- #
def connect_backend(ray_address: str) -> Backend:
    """Pick a backend: Ray if importable and an address is set, else Local.

    ``ray_address`` values: "" → LocalBackend; "local"/"auto" → start a local Ray;
    "ray://host:10001" → attach to a running head.
    """
    address = (ray_address or "").strip()
    if not address:
        logger.info("no ray_address configured; using in-process LocalBackend")
        return LocalBackend()

    try:
        import ray
    except ImportError:
        logger.warning("ray not installed; falling back to LocalBackend (address=%s)", address)
        return LocalBackend()

    init_address = None if address in {"local", "auto"} else address
    if not ray.is_initialized():
        ray.init(address=init_address, ignore_reinit_error=True, logging_level=logging.WARNING)
    logger.info("connected Ray backend (address=%s)", address or "local")
    return RayBackend(ray)
