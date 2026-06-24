from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from mai.publish.dataviz import _source_repos
from mai.publish.me_dashboard import build_me_dashboard
from mai.repository.users import UserRepository
from mai.web.board_api import ensure_csrf


def make_me_router(session_factory) -> APIRouter:
    """GET /api/me — the signed-in user's personal Control Center data."""
    router = APIRouter(prefix="/api/me")

    @router.get("")
    async def get_me(request: Request):
        username = request.session["username"]
        async with session_factory() as session:
            repos = await _source_repos(session)
            dash = await build_me_dashboard(session, username, repos=repos)
            user = await UserRepository(session).get(username)
        dash["is_maintainer"] = bool(user and user.is_maintainer)
        dash["csrf"] = ensure_csrf(request)
        return JSONResponse(dash)

    return router
