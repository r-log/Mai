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
    async with factory() as s:
        pw = await create_account(s, hasher, "dev", is_maintainer=False)
        await s.commit()
    app = create_app(factory, hasher, "test-secret", cookie_secure=False)
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test", follow_redirects=False) as ac:
        yield ac, factory, pw


async def test_full_claim_flow_over_http(env):
    ac, factory, pw = env
    # log in + clear forced password change
    await ac.post("/login", data={"username": "dev", "password": pw})
    await ac.post("/set-password", data={"new_password": "a-good-long-password"})

    # the gated page renders
    page = await ac.get("/port")
    assert page.status_code == 200 and 'id="port-board"' in page.text

    # the API gives us a csrf token
    board = (await ac.get("/api/board")).json()
    token = board["csrf"]
    assert board["me"] == {"username": "dev", "is_maintainer": False}

    # claim a card via the same path the JS uses
    r = await ac.post("/api/board/pgZ:three/claim", json={"csrf": token})
    assert r.status_code == 200
    assert r.json()["assignee"] == "dev"
    assert r.json()["status"] == "claimed"

    # the overlay is now visible to everyone via the API (_orphans here, since
    # pgZ:three is not a real engine candidate in this empty DB)
    after = (await ac.get("/api/board")).json()
    orphan = {o["port_candidate_id"]: o for o in after["_orphans"]}
    assert orphan["pgZ:three"]["assignee"] == "dev"


async def test_mutation_without_csrf_blocked_over_http(env):
    ac, _, pw = env
    await ac.post("/login", data={"username": "dev", "password": pw})
    await ac.post("/set-password", data={"new_password": "a-good-long-password"})
    r = await ac.post("/api/board/pgZ:three/claim", json={})
    assert r.status_code == 403
