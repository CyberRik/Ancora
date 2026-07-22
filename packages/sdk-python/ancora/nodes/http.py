"""HTTPNode — REST calls that are retry-safe and idempotent (AN-052).

The node performs a templated HTTP request and classifies the response so the
runtime does the right thing:

* ``2xx`` → success.
* ``429`` / ``503`` → **transient**, carrying the parsed ``Retry-After`` so the
  scheduler backs off instead of hammering (feeds the no-429-storm guarantee).
* other ``5xx`` → transient (retry with backoff).
* other ``4xx`` → **terminal** (a bad request will never succeed on retry).

Because a non-idempotent method (POST/PUT/PATCH/DELETE) must not fire twice when an
activity is retried, ``idempotent = False``: the ``run_node`` activity wraps this
node in the inbox guard (AN-061), so a replayed call returns the stored response
rather than issuing a second request.

The transport is injectable (a small Protocol) so the node is fully unit-testable
without a network; the default transport lazily uses ``httpx``.
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field

from ancora.nodes.base import Node, NodeContext, NodeError, ResourceHint
from ancora.nodes.registry import register


class HTTPResponse(BaseModel):
    status: int
    headers: dict[str, str] = Field(default_factory=dict)
    text: str = ""

    def json_body(self) -> Any:
        import json

        try:
            return json.loads(self.text) if self.text else None
        except ValueError:
            return None


class HttpTransport(Protocol):
    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, str],
        json: Any | None,
        timeout: float,
    ) -> HTTPResponse: ...


class _HttpxTransport:
    """Default transport — lazily imports httpx so the SDK stays dependency-light."""

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, str],
        json: Any | None,
        timeout: float,
    ) -> HTTPResponse:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - exercised only without httpx
            raise NodeError(
                "HTTPNode default transport requires httpx; install it or inject a transport",
                transient=False,
            ) from exc
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.request(method, url, headers=headers, params=params, json=json)
            return HTTPResponse(
                status=resp.status_code,
                headers={k.lower(): v for k, v in resp.headers.items()},
                text=resp.text,
            )


_transport: HttpTransport = _HttpxTransport()


def set_transport(transport: HttpTransport) -> None:
    """Override the HTTP transport (tests, or a shared pooled client)."""
    global _transport
    _transport = transport


def _render(template: str, vars_: dict[str, str]) -> str:
    """Minimal ``{var}`` substitution; unknown braces are left untouched."""
    out = template
    for key, value in vars_.items():
        out = out.replace("{" + key + "}", value)
    return out


def parse_retry_after(value: str | None) -> float | None:
    """Parse a ``Retry-After`` header (delta-seconds form). ``None`` if absent/HTTP-date."""
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None  # HTTP-date form; MVP relies on backoff instead


class HTTPInput(BaseModel):
    method: str = Field(default="GET", pattern="^(GET|POST|PUT|PATCH|DELETE|HEAD)$")
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    params: dict[str, str] = Field(default_factory=dict)
    json_body: Any | None = None
    timeout_s: float = 30.0
    # Values substituted into ``{var}`` placeholders in url/header values.
    template_vars: dict[str, str] = Field(default_factory=dict)


class HTTPOutput(BaseModel):
    status: int
    headers: dict[str, str]
    json_body: Any | None = None
    text: str


@register
class HTTPNode(Node):
    """A single REST request with retry-after awareness and inbox idempotency."""

    type_name = "http"
    version = "1.0.0"
    summary = "Make a templated REST call; honors Retry-After; exactly-once via inbox."
    input_model = HTTPInput
    output_model = HTTPOutput
    resources = ResourceHint(num_cpus=0.5)
    # Guarded by the inbox so a retried POST fires exactly once (AN-052 AC).
    idempotent = False

    async def execute(self, inp: HTTPInput, ctx: NodeContext) -> HTTPOutput:
        url = _render(inp.url, inp.template_vars)
        headers = {k: _render(v, inp.template_vars) for k, v in inp.headers.items()}

        resp = await _transport.request(
            inp.method,
            url,
            headers=headers,
            params=inp.params,
            json=inp.json_body,
            timeout=inp.timeout_s,
        )

        if resp.status in (429, 503):
            retry_after = parse_retry_after(resp.headers.get("retry-after"))
            raise NodeError(
                f"{inp.method} {url} → {resp.status} (rate-limited/unavailable)",
                transient=True,
                retry_after=retry_after,
            )
        if 500 <= resp.status < 600:
            raise NodeError(f"{inp.method} {url} → {resp.status}", transient=True)
        if 400 <= resp.status < 500:
            raise NodeError(f"{inp.method} {url} → {resp.status}", transient=False)

        return HTTPOutput(
            status=resp.status,
            headers=resp.headers,
            json_body=resp.json_body(),
            text=resp.text,
        )
