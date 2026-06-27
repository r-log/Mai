from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from mai.config import settings
from mai.git.client import LocalGitClient
from mai.sync.review import build_review_evidence


def make_review_router(session_factory, git_client=None) -> APIRouter:
    """GET /api/review/{item_id} — deterministic evidence for one REVIEW item."""
    router = APIRouter(prefix="/api/review")
    client = git_client or LocalGitClient(settings.git_mirror_dir)

    @router.get("/{item_id}")
    async def get_review(request: Request, item_id: str):
        async with session_factory() as session:
            evidence = await build_review_evidence(session, client, item_id)
        return JSONResponse({"evidence": evidence})

    return router
