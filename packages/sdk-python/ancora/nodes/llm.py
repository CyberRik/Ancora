"""LLMNode — multi-provider chat/completion with fallback and cost accounting (AN-051).

An :class:`LLMNode` runs a chat request against a **provider chain**: the primary
provider is tried first, and on a *transient* failure (rate limit, 5xx, timeout)
the node falls back to the next provider in the chain. Every successful call
records token usage and dollar cost to the :class:`NodeContext` (AN-056), so the
workflow can roll up per-run cost (AN-057).

Providers are pluggable via a small adapter interface. The MVP ships a
:class:`MockProvider` so the whole path — including fallback — is exercised in CI
without network access or API keys. Real providers (OpenAI, Anthropic, …) register
the same interface at worker startup.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel, Field

from ancora.nodes.base import Cost, Node, NodeContext, NodeError, ResourceHint
from ancora.nodes.registry import register


class LLMMessage(BaseModel):
    role: str = Field(pattern="^(system|user|assistant)$")
    content: str


class LLMRequest(BaseModel):
    messages: list[LLMMessage]
    model: str
    temperature: float = 0.0
    max_tokens: int = 512


class LLMResponse(BaseModel):
    text: str
    input_tokens: int
    output_tokens: int
    model: str
    provider: str


# --------------------------------------------------------------------------- #
# Provider adapter interface + registry
# --------------------------------------------------------------------------- #
class LLMProvider(ABC):
    """Adapter over a concrete LLM backend. Implementations must be side-effecting
    only inside :meth:`complete` (they run within an activity)."""

    name: str

    @abstractmethod
    async def complete(self, req: LLMRequest) -> LLMResponse:
        """Run one completion. Raise ``NodeError(transient=True)`` for retryables."""

    @abstractmethod
    def price_usd(self, input_tokens: int, output_tokens: int, model: str) -> float:
        """Dollar cost for a call. Providers own their own price tables."""


_PROVIDERS: dict[str, LLMProvider] = {}


def register_provider(provider: LLMProvider) -> None:
    _PROVIDERS[provider.name] = provider


def get_provider(name: str) -> LLMProvider:
    try:
        return _PROVIDERS[name]
    except KeyError:
        raise NodeError(f"no LLM provider registered as '{name}'", transient=False) from None


def clear_providers() -> None:
    """Test helper."""
    _PROVIDERS.clear()


def _tokens(text: str) -> int:
    # Deterministic, whitespace-based token estimate — good enough for accounting
    # and identical across replays. Real providers report exact usage.
    return max(1, len(text.split()))


class MockProvider(LLMProvider):
    """Deterministic provider for CI (AN-051 mock).

    Produces a stable, echoing completion so tests can assert on output. Can be
    told to fail the first ``fail_times`` calls (transiently) to exercise the
    retry/fallback paths.
    """

    def __init__(
        self,
        name: str = "mock",
        *,
        price_per_1k: float = 0.001,
        fail_times: int = 0,
        transient: bool = True,
    ) -> None:
        self.name = name
        self._price_per_1k = price_per_1k
        self._fail_times = fail_times
        self._transient = transient
        self.calls = 0

    async def complete(self, req: LLMRequest) -> LLMResponse:
        self.calls += 1
        if self.calls <= self._fail_times:
            raise NodeError(
                f"{self.name} injected failure #{self.calls}",
                transient=self._transient,
                retry_after=0.0,
            )
        prompt = " ".join(m.content for m in req.messages if m.role != "system")
        text = f"[{self.name}:{req.model}] {prompt.strip()}"[: 40 + req.max_tokens]
        it = sum(_tokens(m.content) for m in req.messages)
        ot = _tokens(text)
        return LLMResponse(
            text=text, input_tokens=it, output_tokens=ot, model=req.model, provider=self.name
        )

    def price_usd(self, input_tokens: int, output_tokens: int, model: str) -> float:
        return (input_tokens + output_tokens) / 1000.0 * self._price_per_1k


# --------------------------------------------------------------------------- #
# The node
# --------------------------------------------------------------------------- #
class LLMInput(BaseModel):
    messages: list[LLMMessage]
    model: str = "mock-small"
    # Provider fallback chain, primary first. Defaults to the CI mock.
    providers: list[str] = Field(default_factory=lambda: ["mock"])
    temperature: float = 0.0
    max_tokens: int = 512


class LLMOutput(BaseModel):
    text: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    usd: float
    # Providers that failed before the one that succeeded (fallback trail).
    fell_back_from: list[str] = Field(default_factory=list)


@register
class LLMNode(Node):
    """Chat/completion with a primary→secondary provider fallback chain."""

    type_name = "llm"
    version = "1.0.0"
    summary = "Chat/completion across providers with fallback and cost accounting."
    input_model = LLMInput
    output_model = LLMOutput
    resources = ResourceHint(num_cpus=1.0)
    idempotent = True  # a completion is safe to re-run (no external side effect)

    async def execute(self, inp: LLMInput, ctx: NodeContext) -> LLMOutput:
        if not inp.providers:
            raise NodeError("LLMNode requires at least one provider", transient=False)

        req = LLMRequest(
            messages=inp.messages,
            model=inp.model,
            temperature=inp.temperature,
            max_tokens=inp.max_tokens,
        )
        tried: list[str] = []
        last: NodeError | None = None

        for name in inp.providers:
            provider = get_provider(name)
            try:
                resp = await provider.complete(req)
            except NodeError as exc:
                ctx.log.warning("llm provider %s failed: %s", name, exc)
                tried.append(name)
                last = exc
                if exc.transient:
                    continue  # fall back to the next provider
                raise  # terminal — do not mask a bad request behind fallback
            usd = provider.price_usd(resp.input_tokens, resp.output_tokens, resp.model)
            ctx.record_cost(
                Cost(
                    usd=usd,
                    input_tokens=resp.input_tokens,
                    output_tokens=resp.output_tokens,
                    provider=resp.provider,
                    model=resp.model,
                )
            )
            return LLMOutput(
                text=resp.text,
                provider=resp.provider,
                model=resp.model,
                input_tokens=resp.input_tokens,
                output_tokens=resp.output_tokens,
                usd=usd,
                fell_back_from=tried,
            )

        raise NodeError(
            f"all LLM providers failed ({', '.join(tried)})",
            transient=True,
            retry_after=last.retry_after if last else None,
        )
