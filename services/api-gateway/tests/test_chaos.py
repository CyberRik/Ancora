"""Chaos injection guardrails.

The Docker daemon is faked. What is worth testing here is not "does httpx post" —
it is the blast radius: that a request cannot reach a container outside this
Compose project, cannot touch anything but the allow-listed worker services, and
cannot do anything at all unless chaos was explicitly enabled.
"""

from __future__ import annotations

from typing import Any

import pytest

from ancora_api.chaos import (
    KILLABLE_SERVICES,
    ChaosDisabledError,
    ChaosLog,
    ChaosService,
    ChaosTargetError,
)


class FakeResponse:
    def __init__(self, payload: Any = None, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeDocker:
    """Stands in for the daemon; records every call so we can assert the target."""

    def __init__(self, containers: list[dict[str, Any]]) -> None:
        self.containers = containers
        self.posts: list[tuple[str, dict[str, Any] | None]] = []

    async def __aenter__(self) -> FakeDocker:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def get(self, path: str, params: Any = None) -> FakeResponse:
        return FakeResponse(self.containers)

    async def post(self, path: str, params: Any = None) -> FakeResponse:
        self.posts.append((path, params))
        return FakeResponse(None, 204)


def container(service: str, *, project: str = "ancora", state: str = "running") -> dict[str, Any]:
    return {
        "Id": f"id-{service}",
        "Names": [f"/{project}-{service}-1"],
        "State": state,
        "Labels": {
            "com.docker.compose.project": project,
            "com.docker.compose.service": service,
        },
    }


def make_service(
    containers: list[dict[str, Any]], *, enabled: bool = True
) -> tuple[ChaosService, FakeDocker]:
    docker = FakeDocker(containers)
    svc = ChaosService(
        enabled=enabled, socket_path="/var/run/docker.sock", project="ancora", log=ChaosLog()
    )
    svc._client = lambda: docker  # type: ignore[method-assign]
    return svc, docker


# --------------------------------------------------------------------------- #
# The off switch
# --------------------------------------------------------------------------- #
async def test_disabled_by_default_refuses_everything() -> None:
    svc, _ = make_service([container("worker")], enabled=False)
    with pytest.raises(ChaosDisabledError):
        await svc.list_targets()
    with pytest.raises(ChaosDisabledError):
        await svc.kill("worker")
    with pytest.raises(ChaosDisabledError):
        await svc.restart("worker")


# --------------------------------------------------------------------------- #
# Blast radius
# --------------------------------------------------------------------------- #
async def test_containers_from_another_compose_project_are_invisible() -> None:
    svc, _ = make_service([container("worker"), container("worker", project="someone-elses-stack")])
    targets = await svc.list_targets()
    assert [t.name for t in targets] == ["ancora-worker-1"]


async def test_infrastructure_containers_are_not_targets() -> None:
    # Killing Postgres or Temporal proves nothing about durable execution; it
    # just breaks the control plane that would have shown you the recovery.
    svc, _ = make_service(
        [container("worker"), container("postgres"), container("temporal"), container("web")]
    )
    assert {t.service for t in await svc.list_targets()} == {"worker"}
    assert "postgres" not in KILLABLE_SERVICES
    assert "temporal" not in KILLABLE_SERVICES


async def test_an_unlisted_service_is_rejected_before_any_docker_call() -> None:
    svc, docker = make_service([container("worker")])
    with pytest.raises(ChaosTargetError, match="not a chaos target"):
        await svc.kill("postgres")
    assert docker.posts == []


async def test_the_api_lists_itself_but_refuses_to_be_killed() -> None:
    svc, docker = make_service([container("api"), container("worker")])
    api_target = next(t for t in await svc.list_targets() if t.service == "api")
    assert api_target.killable is False
    with pytest.raises(ChaosTargetError, match="serves this request"):
        await svc.kill("api")
    assert docker.posts == []


async def test_a_missing_container_is_a_clear_error() -> None:
    svc, _ = make_service([container("worker")])
    with pytest.raises(ChaosTargetError, match="no container found"):
        await svc.kill("activity-worker")


# --------------------------------------------------------------------------- #
# The kill itself
# --------------------------------------------------------------------------- #
async def test_kill_sends_sigkill_to_the_right_container() -> None:
    svc, docker = make_service([container("worker"), container("activity-worker")])
    target = await svc.kill("activity-worker")

    assert target.name == "ancora-activity-worker-1"
    path, params = docker.posts[0]
    assert path == "/containers/id-activity-worker/kill"
    # No drain, no grace period — the failure mode that makes durability interesting.
    assert params == {"signal": "SIGKILL"}


async def test_killing_an_already_dead_container_is_refused() -> None:
    svc, docker = make_service([container("worker", state="exited")])
    with pytest.raises(ChaosTargetError, match="already exited"):
        await svc.kill("worker")
    assert docker.posts == []


async def test_restart_starts_the_container_again() -> None:
    svc, docker = make_service([container("worker", state="exited")])
    await svc.restart("worker")
    assert docker.posts[0][0] == "/containers/id-worker/start"


async def test_injections_are_logged_for_the_recovery_timeline() -> None:
    svc, _ = make_service([container("worker"), container("activity-worker")])
    await svc.kill("worker")
    await svc.kill("activity-worker")

    events = svc.log.recent()
    # Newest first — the UI reads it as a timeline.
    assert [e["service"] for e in events] == ["activity-worker", "worker"]
    assert all(e["action"] == "kill" for e in events)


def test_the_log_is_bounded() -> None:
    log = ChaosLog(limit=3)
    for i in range(10):
        from ancora_api.chaos import ChaosEvent

        log.record(ChaosEvent(action="kill", service=f"s{i}", at=float(i)))
    assert len(log.events) == 3
    assert [e["service"] for e in log.recent()] == ["s9", "s8", "s7"]
