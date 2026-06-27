from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from mai.config import settings
from mai.git.client import LocalGitClient
from mai.sync.review import build_review_advice


def _default_judge():
    if settings.review_advisor_enabled and settings.openrouter_api_key:
        from mai.judge.judge import OpenRouterJudge
        return OpenRouterJudge(settings.openrouter_api_key, settings.openrouter_api_url)
    return None


def make_review_router(session_factory, git_client=None, judge=None) -> APIRouter:
    """GET /api/review/{item_id} — evidence + grounded advisory opinion for a REVIEW item."""
    router = APIRouter(prefix="/api/review")
    client = git_client or LocalGitClient(settings.git_mirror_dir)
    active_judge = judge if judge is not None else _default_judge()

    @router.get("/{item_id}")
    async def get_review(request: Request, item_id: str):
        async with session_factory() as session:
            result = await build_review_advice(session, client, active_judge, item_id)
        return JSONResponse(result)

    return router
