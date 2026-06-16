import httpx
import pytest

from mai.enrich.enricher import OpenRouterEnricher
from mai.enrich.schema import EnrichmentInput, EnrichmentResult, EnrichmentSchemaError

CTX = EnrichmentInput(title="Pet bug", core="zero", source_type="ips", raw_text="threat bug")
GOOD = ('{"normalized_title":"Pet threat","english_summary":"Pet loses threat",'
        '"language_detected":"en"}')


def _ok(request: httpx.Request) -> httpx.Response:
    assert request.headers["Authorization"] == "Bearer or-key"
    assert request.url.path == "/api/v1/chat/completions"
    return httpx.Response(200, json={"choices": [{"message": {"content": GOOD}}]})


def _bad_json(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": "{not json"}}]})


async def test_openrouter_enricher_returns_validated_result():
    async with httpx.AsyncClient(transport=httpx.MockTransport(_ok)) as http:
        enricher = OpenRouterEnricher("or-key", "some/model", client=http)
        result = await enricher.enrich(CTX)
    assert isinstance(result, EnrichmentResult)
    assert result.normalized_title == "Pet threat"
    assert result.language_detected == "en"
    assert enricher.model == "some/model"


async def test_openrouter_enricher_raises_on_bad_json():
    async with httpx.AsyncClient(transport=httpx.MockTransport(_bad_json)) as http:
        enricher = OpenRouterEnricher("or-key", "some/model", client=http)
        with pytest.raises(EnrichmentSchemaError):
            await enricher.enrich(CTX)


def _non_json_body(request: httpx.Request) -> httpx.Response:
    # OpenRouter sometimes returns a 200 whose body is not JSON at all
    # (gateway/error pages, truncated streams). resp.json() then explodes.
    return httpx.Response(200, text="upstream gateway error\n\nplease retry\n")


async def test_openrouter_enricher_raises_on_non_json_body():
    async with httpx.AsyncClient(transport=httpx.MockTransport(_non_json_body)) as http:
        enricher = OpenRouterEnricher("or-key", "some/model", client=http)
        with pytest.raises(EnrichmentSchemaError):
            await enricher.enrich(CTX)


def _missing_choices(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"error": {"message": "rate limited"}})


async def test_openrouter_enricher_raises_on_missing_choices():
    async with httpx.AsyncClient(transport=httpx.MockTransport(_missing_choices)) as http:
        enricher = OpenRouterEnricher("or-key", "some/model", client=http)
        with pytest.raises(EnrichmentSchemaError):
            await enricher.enrich(CTX)


def _http_error(request: httpx.Request) -> httpx.Response:
    return httpx.Response(401, json={"error": "unauthorized"})


async def test_openrouter_enricher_raises_on_http_error():
    async with httpx.AsyncClient(transport=httpx.MockTransport(_http_error)) as http:
        enricher = OpenRouterEnricher("or-key", "some/model", client=http)
        with pytest.raises(httpx.HTTPStatusError):
            await enricher.enrich(CTX)


def _null_content(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": None}}]})


async def test_openrouter_enricher_raises_on_null_content():
    async with httpx.AsyncClient(transport=httpx.MockTransport(_null_content)) as http:
        enricher = OpenRouterEnricher("or-key", "some/model", client=http)
        with pytest.raises(EnrichmentSchemaError):
            await enricher.enrich(CTX)
