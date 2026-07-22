"""Pure compute functions dispatched to a backend, plus their I/O contracts.

Everything here is module-level and picklable so ``RayBackend`` can ship it to a
remote worker. The functions are checkpoint-aware: they resume from ``start_from``
and report a small progress token at each batch boundary, which the driver turns
into a Temporal heartbeat (so a killed activity resumes where it left off, not
from zero — RFC-0001a §8, AN-029).
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import BaseModel, Field

from ancora_activity_worker.ray_bridge import LiveProgress


class ComputeRequest(BaseModel):
    """A 'GPU-ish' unit of work — a stand-in for a real model/inference node."""

    label: str = "compute"
    batches: int = Field(default=4, ge=1)
    # Seconds of simulated work per batch (the demo's "30s activity" = batches*delay).
    batch_seconds: float = Field(default=0.05, ge=0.0)
    num_cpus: float = 1.0
    num_gpus: float = 0.0
    accelerator_type: str | None = None


class ComputeResult(BaseModel):
    label: str
    batches: int
    checksum: int
    backend: str
    resumed_from: int = 0


class _Cancelled(Exception):
    """Raised inside a compute when cooperative cancellation is observed."""


def batched_compute(
    progress: LiveProgress,
    *,
    label: str,
    total_batches: int,
    batch_seconds: float,
    start_from: int,
    acc: int,
) -> dict[str, Any]:
    """Accumulate a checksum over ``total_batches``, checkpointing each batch.

    Resumes at ``start_from`` with the carried ``acc`` so a retry after a crash
    does not redo completed batches.
    """
    checksum = acc
    for i in range(start_from, total_batches):
        if progress.cancelled():
            raise _Cancelled()
        if batch_seconds:
            time.sleep(batch_seconds)
        checksum += (i + 1) * 7
        # Checkpoint AFTER the batch completes: on resume we start at i+1.
        progress.report({"batch": i + 1, "acc": checksum})
    return {
        "label": label,
        "batches": total_batches,
        "checksum": checksum,
        "resumed_from": start_from,
    }
