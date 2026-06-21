import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mai.auth.accounts import create_account
from mai.auth.fake import FakeHasher
from mai.board.service import apply_action
from mai.db.base import Base
from mai.web.app import create_app


@pytest_asyncio.fixture
async def env():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    hasher = FakeHasher()
    pws = {}
    async with factory() as s:
        pws["antz"] = await create_account(s, hasher, "antz", is_maintainer=True)
        pws["dev"] = await create_account(s, hasher, "dev", is_maintainer=False)
        await s.commit()
    app = create_app(factory, hasher, "test-secret", cookie_secure=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test",
                           follow_redirects=False) as ac:
        yield ac, factory, pws


async def _login(ac, username, pw):
    await ac.post("/login", data={"username": username, "password": pw})
    # clear must_change so the gate lets /api/* through
    await ac.post("/set-password", data={"new_password": "a-good-long-password"})


async def test_board_requires_session(env):
    ac, _, _ = env
    r = await ac.get("/api/board")
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


async def test_board_returns_columns_and_csrf_and_me(env):
    ac, _, pws = env
    await _login(ac, "antz", pws["antz"])
    r = await ac.get("/api/board")
    assert r.status_code == 200
    body = r.json()
    assert "columns" in body and "summary" in body
    assert isinstance(body["csrf"], str) and body["csrf"]
    assert body["me"] == {"username": "antz", "is_maintainer": True}


async def test_board_overlays_board_item(env):
    ac, factory, pws = env
    async with factory() as s:
        await apply_action(s, item_id="pgX:three", actor="dev", action="claim")
        await s.commit()
    await _login(ac, "dev", pws["dev"])
    body = (await ac.get("/api/board")).json()
    # find the overlay for our id across columns (candidate may not be in engine data;
    # the overlay endpoint still reports active board items it knows about under _orphans)
    overlays = {o["port_candidate_id"]: o for o in body.get("_orphans", [])}
    assert overlays["pgX:three"]["assignee"] == "dev"
    assert overlays["pgX:three"]["status"] == "claimed"


async def _csrf(ac):
    return (await ac.get("/api/board")).json()["csrf"]


async def test_claim_then_overlay_reflects_it(env):
    ac, _, pws = env
    await _login(ac, "dev", pws["dev"])
    token = await _csrf(ac)
    r = await ac.post("/api/board/pgA:three/claim", json={"csrf": token})
    assert r.status_code == 200
    assert r.json()["assignee"] == "dev"
    assert r.json()["status"] == "claimed"


async def test_csrf_required_for_mutation(env):
    ac, _, pws = env
    await _login(ac, "dev", pws["dev"])
    await _csrf(ac)
    r = await ac.post("/api/board/pgA:three/claim", json={})  # no csrf
    assert r.status_code == 403


async def test_non_maintainer_cannot_assign_or_dismiss(env):
    ac, _, pws = env
    await _login(ac, "dev", pws["dev"])
    token = await _csrf(ac)
    r = await ac.post("/api/board/pgA:three/assign",
                      json={"value": "antz", "csrf": token})
    assert r.status_code == 403
    r2 = await ac.post("/api/board/pgA:three/dismiss",
                       json={"reason": "no", "csrf": token})
    assert r2.status_code == 403


async def test_maintainer_can_assign(env):
    ac, _, pws = env
    await _login(ac, "antz", pws["antz"])
    token = await _csrf(ac)
    r = await ac.post("/api/board/pgA:three/assign",
                      json={"value": "dev", "csrf": token})
    assert r.status_code == 200
    assert r.json()["assignee"] == "dev"


async def test_claim_conflict_returns_409(env):
    ac, factory, pws = env
    async with factory() as s:
        await apply_action(s, item_id="pgB:two", actor="someone", action="claim")
        await s.commit()
    await _login(ac, "dev", pws["dev"])
    token = await _csrf(ac)
    r = await ac.post("/api/board/pgB:two/claim", json={"csrf": token})
    assert r.status_code == 409


async def test_bad_action_returns_400(env):
    ac, _, pws = env
    await _login(ac, "antz", pws["antz"])
    token = await _csrf(ac)
    r = await ac.post("/api/board/pgA:three/frobnicate", json={"csrf": token})
    assert r.status_code == 400
