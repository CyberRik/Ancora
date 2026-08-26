"""The liveness heartbeat must survive the reaper and its own failures.

The registry reaper deletes rows whose heartbeat went cold. A heartbeat that
only *updates* a row cannot undo that, so a live worker would report as absent
forever — which is how the dashboard ends up claiming 1 worker while 3 are
serving traffic. These cover the two ways that loop can lie.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import pytest

from ancora_activity_worker import registration as reg_mod
from ancora_activity_worker.registration import WorkerRegistration
from ancora_activity_worker.settings import ActivityWorkerSettings


class _FakeRegistry:
    """Stands in for ancora_common.registry, recording what the loop asked for."""

    def __init__(self, *, row_present: bool) -> None:
        self.row_present = row_present
        self.liveness_sets = 0
        self.touches = 0
        self.upserts = 0

    def redis_client(self, _url: str) -> object:
        return object()

    async def set_liveness(self, _client: Any, _worker_id: str, _ttl: int) -> None:
        self.liveness_sets += 1

    async def touch_worker_heartbeat(self, _session: Any, _worker_id: str) -> bool:
        self.touches += 1
        return self.row_present

    async def upsert_worker(self, _session: Any, **_kw: Any) -> None:
        self.upserts += 1
        self.row_present = True


class _FakeDb:
    @contextlib.asynccontextmanager
    async def session_scope(self):  # type: ignore[no-untyped-def]
        yield object()


@pytest.fixture
def settings() -> ActivityWorkerSettings:
    return ActivityWorkerSettings(worker_id="aw-test", heartbeat_interval_seconds=0.01)


@pytest.mark.asyncio
async def test_beat_bumps_existing_row_without_reregistering(monkeypatch, settings):
    fake = _FakeRegistry(row_present=True)
    monkeypatch.setattr(reg_mod, "registry", fake)
    monkeypatch.setattr(reg_mod, "db", _FakeDb())

    await WorkerRegistration(settings)._beat()

    assert fake.liveness_sets == 1
    assert fake.touches == 1
    assert fake.upserts == 0, "a healthy worker must not rewrite its registration"


@pytest.mark.asyncio
async def test_beat_reregisters_after_the_reaper_removed_the_row(monkeypatch, settings):
    """The regression: a bump against a deleted row is a silent no-op."""
    fake = _FakeRegistry(row_present=False)
    monkeypatch.setattr(reg_mod, "registry", fake)
    monkeypatch.setattr(reg_mod, "db", _FakeDb())

    worker = WorkerRegistration(settings)
    await worker._beat()
    assert fake.upserts == 1, "a reaped-but-live worker must re-register itself"

    # ...and having healed, it goes back to plain bumps rather than re-upserting
    # its capabilities every five seconds.
    await worker._beat()
    assert fake.upserts == 1


@pytest.mark.asyncio
async def test_loop_outlives_a_failing_beat(monkeypatch, settings):
    """An escaping exception would kill the task silently — nothing awaits it."""
    calls = 0

    async def _flaky(self) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("redis went away")

    monkeypatch.setattr(WorkerRegistration, "_beat", _flaky)
    monkeypatch.setattr(reg_mod, "registry", _FakeRegistry(row_present=True))
    monkeypatch.setattr(reg_mod, "db", _FakeDb())

    worker = WorkerRegistration(settings)
    task = asyncio.create_task(worker._heartbeat_loop())
    while calls < 3:
        await asyncio.sleep(0.01)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert calls >= 3, "the loop stopped after a single failed beat"
