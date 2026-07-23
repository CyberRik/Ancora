"""Client for the scheduler's admission API (AN-038, AN-040).

Used by the activity worker on the node execution path. Two design decisions
carry most of the weight here.

**It fails open.** If the scheduler is unreachable, slow, or returns garbage, the
node is admitted anyway. Admission control is an *optimization* — it protects
providers from 429 storms and queues from overload — while the durability
guarantee is Temporal's. Failing closed would mean a scheduler outage silently
halts every workflow in the fleet, converting a nice-to-have into a
single point of failure. Every fail-open is logged and counted so the degradation
is visible rather than silent.

**Deferral is expressed as a retryable failure.** The worker turns a ``defer``
into a Temporal ``ApplicationError`` carrying ``next_retry_delay``, so the work
goes back into durable history and is re-delivered after the backoff. There is no
queue inside the scheduler and nothing to lose if it restarts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("ancora.scheduler.client")

# A hard ceiling on the admission call itself. The scheduler's decision is an
# in-memory lookup; if it has not answered in this long, it is not healthy and we
# should not be waiting on it.
DEFAULT_TIMEOUT_SECONDS = 2.0


@dataclass(frozen=True)
class Verdict:
    outcome: str  # admit | defer | reject
    rule: str = "none"
    retry_after: float = 0.0
    reason: str = ""
    warning: str | None = None
    timeout_seconds: float | None = None
    # True when the scheduler could not be reached and we admitted by default.
    degraded: bool = False

    @property
    def admitted(self) -> bool:
        return self.outcome == "admit"


ADMIT_OPEN = Verdict(outcome="admit", rule="unavailable", degraded=True)


class SchedulerClient:
    """Thin async client. ``base_url`` of ``None`` disables admission control."""

    def __init__(self, base_url: str | None, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self.base_url = base_url.rstrip("/") if base_url else None
        self.timeout = timeout
        self._client: Any | None = None
        self._degraded_logged = False

    @property
    def enabled(self) -> bool:
        return self.base_url is not None

    async def _http(self) -> Any:
        if self._client is None:
            import httpx

            assert self.base_url is not None  # guarded by `enabled` at every call site
            self._client = httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout)
        return self._client

    async def admit(self, payload: dict[str, Any]) -> Verdict:
        """Ask whether this node may start. Never raises."""
        if not self.enabled:
            return Verdict(outcome="admit", rule="disabled")
        try:
            client = await self._http()
            resp = await client.post("/v1/admit", json=payload)
            resp.raise_for_status()
            body = resp.json()
        except Exception as exc:  # noqa: BLE001 — deliberate fail-open
            if not self._degraded_logged:
                logger.warning("scheduler unreachable (%s); admitting without control", exc)
                self._degraded_logged = True
            return ADMIT_OPEN
        self._degraded_logged = False
        return Verdict(
            outcome=str(body.get("outcome", "admit")),
            rule=str(body.get("rule", "none")),
            retry_after=float(body.get("retry_after", 0.0)),
            reason=str(body.get("reason", "")),
            warning=body.get("warning"),
            timeout_seconds=body.get("timeout_seconds"),
        )

    async def complete(self, payload: dict[str, Any]) -> None:
        """Release the in-flight slot and report spend. Best-effort, never raises."""
        if not self.enabled:
            return
        try:
            client = await self._http()
            await client.post("/v1/complete", json=payload)
        except Exception as exc:  # noqa: BLE001 — a lost report expires by TTL
            logger.debug("scheduler completion report dropped: %s", exc)

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
