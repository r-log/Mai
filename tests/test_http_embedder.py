import json

import httpx
import pytest

from mai.embed.embedder import HttpEmbedder


def _ok(request: httpx.Request) -> httpx.Response:
    assert request.headers["Authorization"] == "Bearer emb-key"
    assert request.url.path == "/v1/embeddings"
    body = json.loads(request.content)
    assert body["model"] == "text-embedding-3-small"
    assert body["input"] == "hello"
    assert body["dimensions"] == 3
    return httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2, 0.3]}]})


def _http_error(request: httpx.Request) -> httpx.Response:
    return httpx.Response(401, json={"error": "unauthorized"})


async def test_http_embedder_returns_vector():
    async with httpx.AsyncClient(transport=httpx.MockTransport(_ok)) as http:
        embedder = HttpEmbedder("emb-key", "text-embedding-3-small", 3, client=http)
        vector = await embedder.embed("hello")
    assert vector == [0.1, 0.2, 0.3]
    assert embedder.model == "text-embedding-3-small"
    assert embedder.dimensions == 3


async def test_http_embedder_raises_on_http_error():
    async with httpx.AsyncClient(transport=httpx.MockTransport(_http_error)) as http:
        embedder = HttpEmbedder("emb-key", "text-embedding-3-small", 3, client=http)
        with pytest.raises(httpx.HTTPStatusError):
            await embedder.embed("hello")
