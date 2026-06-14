from typing import Protocol

import httpx


class Embedder(Protocol):
    @property
    def model(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    async def embed(self, text: str) -> list[float]: ...


class HttpEmbedder:
    """Production Embedder backed by an OpenAI-compatible /v1/embeddings endpoint."""

    def __init__(self, api_key: str, model: str, dimensions: int,
                 base_url: str = "https://api.openai.com",
                 client: httpx.AsyncClient | None = None):
        self._model = model
        self._dimensions = dimensions
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

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, text: str) -> list[float]:
        resp = await self._client.post(
            self._base + "/v1/embeddings",
            json={"model": self._model, "input": text, "dimensions": self._dimensions},
            headers=self._headers,
        )
        resp.raise_for_status()
        return resp.json()["data"][0]["embedding"]
