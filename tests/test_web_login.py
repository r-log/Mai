import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mai.auth.accounts import create_account
from mai.auth.fake import FakeHasher
from mai.db.base import Base
from mai.web.app import create_app


@pytest_asyncio.fixture
async def client_and_pw():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    hasher = FakeHasher()
    async with factory() as s:
        pw = await create_account(s, hasher, "antz", is_maintainer=True)
        await s.commit()
    app = create_app(factory, hasher, "test-secret", cookie_secure=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test",
                           follow_redirects=False) as ac:
        yield ac, pw
    await engine.dispose()


async def test_home_requires_login(client_and_pw):
    ac, _ = client_and_pw
    r = await ac.get("/")
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


async def test_login_page_is_public(client_and_pw):
    ac, _ = client_and_pw
    r = await ac.get("/login")
    assert r.status_code == 200
    assert "password" in r.text.lower()


async def test_login_wrong_password_is_generic_401(client_and_pw):
    ac, _ = client_and_pw
    r = await ac.post("/login", data={"username": "antz", "password": "nope"})
    assert r.status_code == 401
    assert "invalid username or password" in r.text.lower()


async def test_login_unknown_user_is_generic_401(client_and_pw):
    ac, _ = client_and_pw
    r = await ac.post("/login", data={"username": "ghost", "password": "x"})
    assert r.status_code == 401
    assert "invalid username or password" in r.text.lower()


async def test_login_success_redirects_must_change_user_to_set_password(client_and_pw):
    ac, pw = client_and_pw
    r = await ac.post("/login", data={"username": "antz", "password": pw})
    assert r.status_code == 303
    assert r.headers["location"] == "/set-password"


async def test_logout_clears_session(client_and_pw):
    ac, pw = client_and_pw
    await ac.post("/login", data={"username": "antz", "password": pw})
    r = await ac.post("/logout")
    assert r.status_code == 303
    assert r.headers["location"] == "/login"
    after = await ac.get("/")
    assert after.status_code == 303 and after.headers["location"] == "/login"
