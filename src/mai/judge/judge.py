# src/mai/judge/judge.py
from typing import Protocol

import httpx

from mai.judge.prompt import SYSTEM_PROMPT, build_prompt
from mai.judge.schema import ReviewOpinion, ReviewOpinionSchemaError, parse_opinion


class ReviewJudge(Protocol):
    async def judge(self, evidence: dict, model: str) -> ReviewOpinion: ...


def choose_model(evidence: dict, settings) -> str:
    """Pick the large-context model for many-hunk / large fixes, else the default."""
    conflict = evidence.get("conflict") or {}
    total = conflict.get("total") or 0
    size = 0
    for h in conflict.get("hunks") or []:
        size += len(h.get("patch_text") or "") + len(h.get("target_context") or "")
    if (total > settings.review_hunk_routing_threshold
            or size > settings.review_large_context_chars):
        return settings.review_model_large
    return settings.review_model


class OpenRouterJudge:
    """Production ReviewJudge backed by OpenRouter chat completions."""

    def __init__(self, api_key: str, base_url: str = "https://openrouter.ai/api",
                 client: httpx.AsyncClient | None = None):
        self._base = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient()
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    async def judge(self, evidence: dict, model: str) -> ReviewOpinion:
        resp = await self._client.post(
            self._base + "/v1/chat/completions",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": build_prompt(evidence)},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0,
            },
            headers=self._headers,
        )
        resp.raise_for_status()
        try:
            content = resp.json()["choices"][0]["message"].get("content")
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ReviewOpinionSchemaError(f"malformed OpenRouter response: {exc}") from exc
        if not content:
            raise ReviewOpinionSchemaError("OpenRouter returned empty/null message content")
        return parse_opinion(content)
