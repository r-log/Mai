import secrets

from fastapi import APIRouter, Request

from mai.publish.dataviz import build_port_candidates
from mai.repository.board import BoardItemRepository
from mai.repository.users import UserRepository


def ensure_csrf(request: Request) -> str:
    token = request.session.get("csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
    return token


def _overlay(item) -> dict:
    return {"assignee": item.assignee, "status": item.status,
            "related_pr": item.related_pr,
            "dismissed": item.status == "dismissed",
            "dismiss_reason": item.dismiss_reason}


def make_board_router(session_factory) -> APIRouter:
    router = APIRouter(prefix="/api/board")

    @router.get("")
    async def get_board(request: Request):
        username = request.session["username"]
        async with session_factory() as session:
            board = await build_port_candidates(session)
            items = {bi.port_candidate_id: bi
                     for bi in await BoardItemRepository(session).active()}
            user = await UserRepository(session).get(username)

        seen = set()
        for col in board["columns"]:
            for cand in col["candidates"]:
                bi = items.get(cand["id"])
                cand["board"] = _overlay(bi) if bi else None
                if bi:
                    seen.add(cand["id"])
        # board items with no matching open candidate (e.g. just-claimed test ids)
        board["_orphans"] = [
            {"port_candidate_id": pcid, **_overlay(bi)}
            for pcid, bi in items.items() if pcid not in seen
        ]
        board["csrf"] = ensure_csrf(request)
        board["me"] = {"username": username,
                       "is_maintainer": bool(user and user.is_maintainer)}
        return board

    return router
