from typing import Protocol

import httpx

from mai.enrich.prompt import SYSTEM_PROMPT, build_prompt
from mai.enrich.schema import EnrichmentInput, EnrichmentResult, parse_enrichment


class Enricher(Protocol):
    @property
    def model(self) -> str: ...

    async def enrich(self, ctx: EnrichmentInput) -> EnrichmentResult: ...


class OpenRouterEnricher:
    """Production Enricher backed by OpenRouter chat completions."""

    def __init__(self, api_key: str, model: str,
                 base_url: str = "https://openrouter.ai",
                 client: httpx.AsyncClient | None = None):
        self._model = model
        self._base = base_url.rstrip("/")
        # caller should inject a managed client (async with ...) in production
        self._client = client or httpx.AsyncClient()
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    @property
    def model(self) -> str:
        return self._model

    async def enrich(self, ctx: EnrichmentInput) -> EnrichmentResult:
        resp = await self._client.post(
            self._base + "/v1/chat/completions",
            json={
                "model": self._model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": build_prompt(ctx)},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0,
            },
            headers=self._headers,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        return parse_enrichment(content)
