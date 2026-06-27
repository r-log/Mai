# tests/test_review_judge.py
import httpx
import pytest

from types import SimpleNamespace
from mai.judge.judge import OpenRouterJudge, choose_model
from mai.judge.fake import FakeJudge
from mai.judge.schema import ReviewOpinion, ReviewOpinionSchemaError

GOOD = ('{"assessment":"portable","confidence":0.7,"reason":"clean apply",'
        '"tips":["adapt in src/x.cpp"],"citations":["src/x.cpp"]}')

SETTINGS = SimpleNamespace(review_model="anthropic/sonnet",
                           review_model_large="google/gemini",
                           review_hunk_routing_threshold=8,
                           review_large_context_chars=24000)


def _small():
    return {"conflict": {"total": 2, "hunks": [{"patch_text": "x", "target_context": "y"}]}}


def _many_hunks():
    return {"conflict": {"total": 20, "hunks": [{"patch_text": "x", "target_context": ""}]}}


def _huge_context():
    return {"conflict": {"total": 1,
                         "hunks": [{"patch_text": "a" * 30000, "target_context": ""}]}}


def test_choose_model_small_picks_default():
    assert choose_model(_small(), SETTINGS) == "anthropic/sonnet"


def test_choose_model_many_hunks_picks_large():
    assert choose_model(_many_hunks(), SETTINGS) == "google/gemini"


def test_choose_model_huge_context_picks_large():
    assert choose_model(_huge_context(), SETTINGS) == "google/gemini"


def _ok(request: httpx.Request) -> httpx.Response:
    assert request.headers["Authorization"] == "Bearer or-key"
    assert request.url.path == "/api/v1/chat/completions"
    body = request.read().decode()
    assert '"model":"some/model"' in body.replace(" ", "")
    assert '"temperature":0' in body.replace(" ", "")
    return httpx.Response(200, json={"choices": [{"message": {"content": GOOD}}]})


def _bad(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": "{nope"}}]})


def _non_json(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, text="gateway error")


async def test_openrouter_judge_returns_validated_opinion():
    async with httpx.AsyncClient(transport=httpx.MockTransport(_ok)) as http:
        judge = OpenRouterJudge("or-key", client=http)
        op = await judge.judge({"fix": {}, "conflict": {"hunks": []}}, "some/model")
    assert isinstance(op, ReviewOpinion)
    assert op.assessment == "portable"


async def test_openrouter_judge_raises_on_bad_json():
    async with httpx.AsyncClient(transport=httpx.MockTransport(_bad)) as http:
        judge = OpenRouterJudge("or-key", client=http)
        with pytest.raises(ReviewOpinionSchemaError):
            await judge.judge({"fix": {}, "conflict": {"hunks": []}}, "some/model")


async def test_openrouter_judge_raises_on_non_json_body():
    async with httpx.AsyncClient(transport=httpx.MockTransport(_non_json)) as http:
        judge = OpenRouterJudge("or-key", client=http)
        with pytest.raises(ReviewOpinionSchemaError):
            await judge.judge({"fix": {}, "conflict": {"hunks": []}}, "some/model")


def test_openrouter_judge_default_client_has_generous_timeout():
    # httpx defaults to 5s, which times out on real LLM latency (esp. the large model).
    j = OpenRouterJudge("or-key")
    assert j._client.timeout.read is not None and j._client.timeout.read >= 60


async def test_fake_judge_records_model_and_counts_calls():
    fake = FakeJudge()
    op = await fake.judge({"x": 1}, "anthropic/sonnet")
    assert isinstance(op, ReviewOpinion)
    assert fake.calls == 1 and fake.last_model == "anthropic/sonnet"


def test_build_prompt_not_truncated_below_large_routing():
    from mai.judge.prompt import build_prompt
    # evidence larger than the 24000 routing threshold (would route to the large model)
    big = {"conflict": {"hunks": [{"patch_text": "Z" * 50000, "target_context": ""}]}}
    out = build_prompt(big)
    assert len(out) > 40000   # NOT cut down to the old 24000 cap
