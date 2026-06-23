import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mai.auth.accounts import create_account
from mai.auth.fake import FakeHasher
from mai.db.base import Base
from mai.web.app import create_app


@pytest_asyncio.fixture
async def client_pw():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    hasher = FakeHasher()
    async with factory() as s:
        pw = await create_account(s, hasher, "antz", is_maintainer=True)
        await s.commit()
    app = create_app(factory, hasher, "test-secret", cookie_secure=False)
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test", follow_redirects=False) as ac:
        yield ac, pw


async def _login(ac, pw):
    await ac.post("/login", data={"username": "antz", "password": pw})
    await ac.post("/set-password", data={"new_password": "a-good-long-password"})


async def test_port_requires_session(client_pw):
    ac, _ = client_pw
    r = await ac.get("/port")
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


async def test_root_redirects_to_port(client_pw):
    ac, pw = client_pw
    await _login(ac, pw)
    r = await ac.get("/")
    assert r.status_code == 303
    assert r.headers["location"] == "/port"


async def test_port_shell_has_mount_points(client_pw):
    ac, pw = client_pw
    await _login(ac, pw)
    r = await ac.get("/port")
    assert r.status_code == 200
    body = r.text
    for marker in ['id="cc-radar"', 'id="cc-summary"', 'id="ready-list"',
                   'id="review-list"', '/static/portboard.js', '/static/board.css']:
        assert marker in body


async def test_static_assets_served(client_pw):
    ac, pw = client_pw
    await _login(ac, pw)  # static is public, but logging in is harmless
    css = await ac.get("/static/board.css")
    js = await ac.get("/static/portboard.js")
    assert css.status_code == 200
    assert js.status_code == 200
