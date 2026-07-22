"""Unit tests for the execution-backend abstraction (LocalBackend + resources)."""

from __future__ import annotations

import functools
import threading
import time

from ancora_activity_worker.ray_bridge import LiveProgress, LocalBackend, connect_backend
from ancora_activity_worker.tasks import Cancelled, batched_compute
from ancora_common.resources import Capability, ResourceSpec


def _run(fn, progress: LiveProgress):
    backend = LocalBackend()
    task = backend.submit(fn, resources=ResourceSpec(), progress=progress)
    return task


def test_local_backend_round_trips() -> None:
    progress = LiveProgress()
    fn = functools.partial(
        batched_compute, label="x", total_batches=5, batch_seconds=0.0, start_from=0, acc=0
    )
    task = _run(fn, progress)
    result = task.result()
    assert result["batches"] == 5
    # checksum = sum((i+1)*7 for i in range(5)) = 7*(1+2+3+4+5) = 105
    assert result["checksum"] == 105
    assert progress.latest() == {"batch": 5, "acc": 105}


def test_local_backend_cooperative_cancel() -> None:
    progress = LiveProgress()
    started = threading.Event()

    def slow(p: LiveProgress) -> dict:
        started.set()
        return batched_compute(
            p, label="x", total_batches=1000, batch_seconds=0.01, start_from=0, acc=0
        )

    task = _run(slow, progress)
    assert started.wait(1.0)
    time.sleep(0.05)
    task.cancel()
    # The cooperative stop surfaces as Cancelled from the compute.
    try:
        task.result()
    except Cancelled:
        pass
    except Exception:  # future may also report cancellation
        pass
    # It must not have run all 1000 batches.
    latest = progress.latest()
    assert latest is None or latest["batch"] < 1000


def test_resource_spec_to_ray_options() -> None:
    spec = ResourceSpec(num_cpus=2, num_gpus=1, accelerator_type="A100", memory_mb=512)
    opts = spec.to_ray_options()
    assert opts["num_cpus"] == 2
    assert opts["num_gpus"] == 1
    assert opts["accelerator_type"] == "A100"
    assert opts["memory"] == 512 * 1024 * 1024
    assert spec.capability is Capability.GPU
    assert ResourceSpec().capability is Capability.CPU


def test_connect_backend_falls_back_to_local() -> None:
    # Empty address → LocalBackend, no Ray import required.
    backend = connect_backend("")
    assert backend.name == "local"
