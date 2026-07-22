"""Capability classes, task-queue routing, and Ray resource specs.

A *capability* is the kind of hardware/isolation a node needs (cpu / gpu / io).
It maps 1:1 to the Temporal task queue the activity is scheduled on, so a worker
that only serves ``cpu`` never receives ``gpu`` work (AN-033). The ``ResourceSpec``
is the Ray-facing request (num_cpus/num_gpus/accelerator_type) derived from a
node's declared needs (AN-034).

This module is import-safe under the workflow sandbox: it pulls in neither Ray
nor Temporal, only stdlib. Workflow code uses :func:`queue_for` to route a call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Final


class Capability(str, Enum):
    """The class of worker a node must run on."""

    CPU = "cpu"
    GPU = "gpu"
    IO = "io"


# Capability → task queue. Kept as an explicit table (not f-strings) so the set of
# real queues is greppable and stable across the API, workers, and scheduler.
_QUEUE_BY_CAPABILITY: Final[dict[Capability, str]] = {
    Capability.CPU: "ancora-cpu",
    Capability.GPU: "ancora-gpu",
    Capability.IO: "ancora-io",
}
_CAPABILITY_BY_QUEUE: Final[dict[str, Capability]] = {
    q: c for c, q in _QUEUE_BY_CAPABILITY.items()
}

# The queue the workflow (orchestration) workers poll. Separate from activity
# capability queues so orchestration is never starved by heavy compute.
WORKFLOW_TASK_QUEUE: Final = "ancora-default"

# All capability queues, in a stable order (used by GET /v1/queues).
ALL_CAPABILITY_QUEUES: Final[tuple[str, ...]] = tuple(_QUEUE_BY_CAPABILITY.values())


def queue_for(capability: Capability | str) -> str:
    """Return the task queue an activity of this capability is scheduled on."""
    cap = Capability(capability)
    return _QUEUE_BY_CAPABILITY[cap]


def capability_for(queue: str) -> Capability | None:
    """Inverse of :func:`queue_for`; ``None`` for the workflow/unknown queue."""
    return _CAPABILITY_BY_QUEUE.get(queue)


@dataclass(frozen=True)
class ResourceSpec:
    """A Ray resource request for a single activity dispatch.

    ``num_cpus``/``num_gpus`` feed Ray's scheduler directly; Ray's own accounting
    prevents over-subscription (a 1-GPU node only lands where a GPU is free).
    """

    num_cpus: float = 1.0
    num_gpus: float = 0.0
    accelerator_type: str | None = None
    memory_mb: int | None = None

    @property
    def capability(self) -> Capability:
        """The capability class implied by this resource request."""
        return Capability.GPU if self.num_gpus > 0 else Capability.CPU

    def to_ray_options(self) -> dict[str, Any]:
        """Translate to kwargs for ``ray.remote(**opts)`` (only set what's asked)."""
        opts: dict[str, Any] = {"num_cpus": self.num_cpus}
        if self.num_gpus:
            opts["num_gpus"] = self.num_gpus
        if self.accelerator_type:
            opts["accelerator_type"] = self.accelerator_type
        if self.memory_mb:
            opts["memory"] = int(self.memory_mb) * 1024 * 1024
        return opts

    def to_dict(self) -> dict[str, Any]:
        return {
            "num_cpus": self.num_cpus,
            "num_gpus": self.num_gpus,
            "accelerator_type": self.accelerator_type,
            "memory_mb": self.memory_mb,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> ResourceSpec:
        if not data:
            return cls()
        return cls(
            num_cpus=float(data.get("num_cpus", 1.0)),
            num_gpus=float(data.get("num_gpus", 0.0)),
            accelerator_type=data.get("accelerator_type"),
            memory_mb=data.get("memory_mb"),
        )


@dataclass
class WorkerCapabilities:
    """What a single activity worker advertises to the registry (AN-032)."""

    worker_id: str
    pools: list[Capability] = field(default_factory=list)
    total_cpus: float = 0.0
    total_gpus: float = 0.0
    accelerator_type: str | None = None

    @property
    def task_queues(self) -> list[str]:
        return [queue_for(p) for p in self.pools]

    def to_dict(self) -> dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "pools": [p.value for p in self.pools],
            "task_queues": self.task_queues,
            "total_cpus": self.total_cpus,
            "total_gpus": self.total_gpus,
            "accelerator_type": self.accelerator_type,
        }
