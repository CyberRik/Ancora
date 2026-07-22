import json
import os
import logging

from ancora.nodes.base import NodeError
from ancora.nodes.llm import LLMProvider, LLMRequest, LLMResponse, _tokens

logger = logging.getLogger("ancora.nodes.gemini")

class GeminiProvider(LLMProvider):
    """Real LLM provider using Google Gemini's API."""

    name = "gemini"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY")
        if not self.api_key:
            logger.warning("GEMINI_API_KEY is not set. Real LLM calls will fail.")

    async def complete(self, req: LLMRequest) -> LLMResponse:
        import httpx
        
        if not self.api_key:
            raise NodeError("GEMINI_API_KEY is missing", transient=False)

        # Default to a valid Gemini model if a mock model name was used
        model = req.model
        if "mock" in model.lower():
            model = "gemini-3.5-flash-lite"
            
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={self.api_key}"
        headers = {
            "Content-Type": "application/json",
        }
        
        contents = []
        for m in req.messages:
            role = "user" if m.role == "user" else "model"
            # system messages are technically handled differently in newer Gemini API (systemInstruction), 
            # but mapping to "user" or "model" works for basic chat for now if we prepend it or just send it as user.
            if m.role == "system":
                # For simplicity, we just treat system as user prompt to avoid Gemini systemInstruction strictness
                contents.append({"role": "user", "parts": [{"text": "System Instructions: " + m.content}]})
            else:
                contents.append({"role": role, "parts": [{"text": m.content}]})
                
        payload = {
            "contents": contents,
            "generationConfig": {
                "temperature": req.temperature,
                "maxOutputTokens": req.max_tokens,
            }
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                resp = await client.post(url, headers=headers, json=payload)
            except httpx.RequestError as exc:
                raise NodeError(f"Gemini network error: {exc}", transient=True, retry_after=5.0) from exc

        if resp.status_code == 429 or resp.status_code >= 500:
            raise NodeError(
                f"Gemini transient error {resp.status_code}: {resp.text}",
                transient=True,
                retry_after=float(resp.headers.get("retry-after", 5.0)),
            )
        if resp.status_code != 200:
            raise NodeError(
                f"Gemini terminal error {resp.status_code}: {resp.text}",
                transient=False,
            )

        data = resp.json()
        try:
            candidates = data.get("candidates", [])
            if not candidates:
                raise NodeError("Gemini returned no candidates", transient=False)
            text = candidates[0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError) as e:
            raise NodeError(f"Unexpected Gemini response structure: {data}", transient=False) from e
        
        usage = data.get("usageMetadata", {})
        it = usage.get("promptTokenCount", sum(_tokens(m.content) for m in req.messages))
        ot = usage.get("candidatesTokenCount", _tokens(text))

        return LLMResponse(
            text=text,
            input_tokens=it,
            output_tokens=ot,
            model=model,
            provider=self.name,
        )

    def price_usd(self, input_tokens: int, output_tokens: int, model: str) -> float:
        # Rough pricing for gemini-2.5-flash
        # $0.075 per 1M input tokens, $0.30 per 1M output tokens
        if "flash" in model.lower():
            return (input_tokens / 1_000_000 * 0.075) + (output_tokens / 1_000_000 * 0.30)
        # Fallback approximation for other models
        return (input_tokens + output_tokens) / 1000.0 * 0.001
