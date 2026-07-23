"""Chaos injection: kill a worker from the UI (AN-072 groundwork, Phase 3 scope).

Ancora's whole claim is "kill any worker mid-run and the workflow recovers". A
claim you have to take on faith is worth very little, so this makes it something
a visitor can *do* — press a button, watch a real process die, watch the run
finish anyway.

**The kill is real.** No simulation, no cooperative shutdown handshake, no
pretending. The API asks the Docker daemon to `SIGKILL` the container; the worker
gets no chance to drain, ack, or tidy up, which is exactly the failure mode that
makes durable execution interesting. A worker that agreed to die politely would
prove nothing.

**It is off by default.** Reaching the Docker socket means the API can control
its own host's containers — real privilege, and not something to hand out
implicitly. It is enabled only when ``ANCORA_CHAOS_ENABLED`` is set, which the
local compose stack does and nothing else should. Every request is also scoped to
one Compose project and an explicit allow-list of service names, so even with the
socket mounted this cannot touch the database, Temporal, or anything outside the
blast radius it advertises.

Docker is reached over its Unix socket with plain HTTP — no docker SDK
dependency, since the three calls needed here are one-liners.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("ancora.chaos")

# Only these Compose services may be targeted. Killing Postgres or Temporal would
# not demonstrate durable execution — it would just break the control plane.
KILLABLE_SERVICES: frozenset[str] = frozenset({"worker", "activity-worker", "scheduler", "api"})

# The daemon is local; if it has not answered in this long something is wrong
# with the host, and the UI should say so rather than hang.
DOCKER_TIMEOUT_SECONDS = 10.0


class ChaosDisabledError(Exception):
    """Raised when chaos injection is not enabled for this deployment."""


class ChaosTargetError(Exception):
    """Raised for an unknown, disallowed, or absent target."""


@dataclass(frozen=True)
class ChaosTarget:
    service: str
    container_id: str
    name: str
    state: str  # running | exited | ...
    # False for the API itself: it can be killed, but it cannot report on it.
    killable: bool = True


@dataclass
class ChaosEvent:
    """One injection, kept in memory so the UI can render a recovery timeline."""

    action: str
    service: str
    at: float
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "service": self.service,
            "at": self.at,
            "detail": self.detail,
        }


@dataclass
class ChaosLog:
    """A short rolling history of injections (the API is stateless otherwise)."""

    limit: int = 50
    events: list[ChaosEvent] = field(default_factory=list)

    def record(self, event: ChaosEvent) -> None:
        self.events.append(event)
        del self.events[: max(0, len(self.events) - self.limit)]

    def recent(self) -> list[dict[str, Any]]:
        return [e.to_dict() for e in reversed(self.events)]


class ChaosService:
    """Talks to the local Docker daemon over its Unix socket."""

    def __init__(
        self,
        *,
        enabled: bool,
        socket_path: str,
        project: str,
        log: ChaosLog,
    ) -> None:
        self.enabled = enabled
        self.socket_path = socket_path
        self.project = project
        self.log = log

    def _require_enabled(self) -> None:
        if not self.enabled:
            raise ChaosDisabledError(
                "chaos injection is disabled; set ANCORA_CHAOS_ENABLED=true and mount "
                "the Docker socket (the local compose stack does both)"
            )

    def _client(self) -> Any:
        import httpx

        # The host in the URL is ignored for a UDS transport but httpx requires one.
        return httpx.AsyncClient(
            base_url="http://docker",
            transport=httpx.AsyncHTTPTransport(uds=self.socket_path),
            timeout=DOCKER_TIMEOUT_SECONDS,
        )

    async def list_targets(self) -> list[ChaosTarget]:
        """Containers in this Compose project, with their current state."""
        self._require_enabled()
        async with self._client() as client:
            resp = await client.get("/containers/json", params={"all": "true"})
            resp.raise_for_status()
            containers = resp.json()

        targets: list[ChaosTarget] = []
        for c in containers:
            labels: dict[str, str] = c.get("Labels") or {}
            if labels.get("com.docker.compose.project") != self.project:
                continue
            service = labels.get("com.docker.compose.service", "")
            if service not in KILLABLE_SERVICES:
                continue
            targets.append(
                ChaosTarget(
                    service=service,
                    container_id=str(c.get("Id", "")),
                    name=str((c.get("Names") or ["?"])[0]).lstrip("/"),
                    state=str(c.get("State", "unknown")),
                    # Killing the API kills the endpoint reporting the result, so
                    # the UI would lose the very thing it is trying to show.
                    killable=service != "api",
                )
            )
        return sorted(targets, key=lambda t: t.service)

    async def _resolve(self, service: str) -> ChaosTarget:
        if service not in KILLABLE_SERVICES:
            raise ChaosTargetError(
                f"'{service}' is not a chaos target; allowed: {', '.join(sorted(KILLABLE_SERVICES))}"
            )
        for target in await self.list_targets():
            if target.service == service:
                return target
        raise ChaosTargetError(
            f"no container found for service '{service}' in project '{self.project}'"
        )

    async def kill(self, service: str, *, signal: str = "SIGKILL") -> ChaosTarget:
        """Kill a worker container outright. No drain, no goodbye."""
        self._require_enabled()
        target = await self._resolve(service)
        if not target.killable:
            raise ChaosTargetError(
                f"'{service}' cannot be killed from the UI — it serves this request"
            )
        if target.state != "running":
            raise ChaosTargetError(f"'{service}' is already {target.state}")

        async with self._client() as client:
            resp = await client.post(
                f"/containers/{target.container_id}/kill", params={"signal": signal}
            )
            resp.raise_for_status()

        logger.warning("chaos: killed %s (%s) with %s", service, target.name, signal)
        self.log.record(
            ChaosEvent(
                action="kill",
                service=service,
                at=time.time(),
                detail=f"{signal} → {target.name}",
            )
        )
        return target

    async def restart(self, service: str) -> ChaosTarget:
        """Start a killed container again.

        Docker treats a manual kill as intentional, so ``restart: on-failure``
        does not fire — recovery is explicit, which is the honest behaviour to
        show: the *run* recovers by itself, the *host* does not.
        """
        self._require_enabled()
        target = await self._resolve(service)
        async with self._client() as client:
            resp = await client.post(f"/containers/{target.container_id}/start")
            # 304 = already running, which is a no-op, not an error.
            if resp.status_code != 304:
                resp.raise_for_status()

        logger.info("chaos: restarted %s (%s)", service, target.name)
        self.log.record(
            ChaosEvent(action="restart", service=service, at=time.time(), detail=target.name)
        )
        return target
