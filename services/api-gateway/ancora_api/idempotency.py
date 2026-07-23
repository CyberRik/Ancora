"""``Idempotency-Key`` middleware for unsafe HTTP requests (AN-066).

A client that times out on ``POST /v1/workflows/x/runs`` cannot know whether the
run started. Retrying risks a duplicate; not retrying risks losing the request.
The standard answer is an idempotency key: the client sends a key, the server
promises that the same key produces the same outcome exactly once.

Ancora already honours the key for run creation *semantically* — the key becomes
the Temporal workflow id, and Temporal refuses a second workflow with the same id
(``WorkflowAlreadyStartedError`` → the existing run is returned). This middleware
generalizes that to every mutating endpoint and, just as importantly, makes the
**replay** cheap: a repeated key returns the stored response without touching
Temporal or the database at all.

Three behaviours worth stating explicitly:

* **In-flight duplicates get 409, not a second execution.** If the first request
  is still running when the retry arrives, the retry is rejected rather than
  queued — the client should wait and re-poll, not double-submit.
* **Only successful responses are cached.** Replaying a 500 would make a
  transient failure permanent for that key.
* **Entries expire.** Keys are held for a bounded window (long enough to cover
  any sane client retry), so this cannot grow without limit. It is a per-process
  cache; the durable guarantee still comes from Temporal's workflow-id
  uniqueness, which survives an API restart and holds across replicas.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

# How long a completed response stays replayable. Comfortably longer than any
# reasonable client retry budget, short enough to bound memory.
DEFAULT_TTL_SECONDS = 24 * 60 * 60
_MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})


@dataclass
class _Entry:
    created_at: float
    status_code: int | None = None
    body: bytes | None = None
    media_type: str | None = None

    @property
    def in_flight(self) -> bool:
        return self.status_code is None


@dataclass
class IdempotencyStore:
    """TTL cache of completed responses keyed by ``(method, path, key)``."""

    ttl_seconds: float = DEFAULT_TTL_SECONDS
    _entries: dict[str, _Entry] = field(default_factory=dict)

    def _sweep(self) -> None:
        cutoff = time.monotonic() - self.ttl_seconds
        for k in [k for k, e in self._entries.items() if e.created_at < cutoff]:
            del self._entries[k]

    def begin(self, key: str) -> _Entry | None:
        """Reserve ``key``. Returns the existing entry when this is a duplicate."""
        self._sweep()
        existing = self._entries.get(key)
        if existing is not None:
            return existing
        self._entries[key] = _Entry(created_at=time.monotonic())
        return None

    def finish(self, key: str, *, status_code: int, body: bytes, media_type: str | None) -> None:
        entry = self._entries.get(key)
        if entry is None:
            return
        if status_code >= 400:
            # Do not make a transient failure permanent for this key.
            del self._entries[key]
            return
        entry.status_code = status_code
        entry.body = body
        entry.media_type = media_type

    def abandon(self, key: str) -> None:
        """Release a reservation whose handler raised, so a retry can proceed."""
        self._entries.pop(key, None)


class IdempotencyMiddleware(BaseHTTPMiddleware):
    """Replays the stored response for a repeated ``Idempotency-Key``."""

    def __init__(self, app: object, store: IdempotencyStore | None = None) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self.store = store or IdempotencyStore()

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        key_header = request.headers.get("Idempotency-Key")
        if not key_header or request.method not in _MUTATING:
            return await call_next(request)

        cache_key = f"{request.method}:{request.url.path}:{key_header}"
        existing = self.store.begin(cache_key)
        if existing is not None:
            if existing.in_flight:
                return JSONResponse(
                    status_code=409,
                    content={
                        "detail": (
                            "a request with this Idempotency-Key is still in flight; "
                            "wait and re-read the resource instead of resubmitting"
                        )
                    },
                    headers={"Idempotency-Status": "in-flight"},
                )
            return Response(
                content=existing.body or b"",
                status_code=existing.status_code or 200,
                media_type=existing.media_type,
                headers={"Idempotency-Status": "replayed"},
            )

        try:
            response = await call_next(request)
        except Exception:
            self.store.abandon(cache_key)
            raise

        # Buffer the body so it can be both stored and returned; responses on
        # these endpoints are small JSON documents, never streams.
        chunks = [chunk async for chunk in response.body_iterator]  # type: ignore[attr-defined]
        body = b"".join(chunks)
        self.store.finish(
            cache_key,
            status_code=response.status_code,
            body=body,
            media_type=response.media_type,
        )
        headers = dict(response.headers)
        headers.pop("content-length", None)
        return Response(
            content=body,
            status_code=response.status_code,
            headers=headers,
            media_type=response.media_type,
        )
