import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mai.auth.accounts import create_account
from mai.auth.fake import FakeHasher
from mai.db.base import Base
from mai.web.app import _home_html, create_app


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


class SpyHasher:
    """Wraps FakeHasher and counts verify() calls."""

    def __init__(self) -> None:
        self._inner = FakeHasher()
        self.verify_calls = 0

    def hash(self, password: str) -> str:
        return self._inner.hash(password)

    def verify(self, password: str, hashed: str) -> bool:
        self.verify_calls += 1
        return self._inner.verify(password, hashed)


async def test_unknown_user_still_calls_verify():
    """Dummy verify must run even when the username is not found (timing fix)."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        from mai.db.base import Base as _Base
        await conn.run_sync(_Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    spy = SpyHasher()
    app = create_app(factory, spy, "test-secret", cookie_secure=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test",
                           follow_redirects=False) as ac:
        r = await ac.post("/login",
                          data={"username": "nobody", "password": "x"})
    await engine.dispose()
    assert r.status_code == 401
    assert spy.verify_calls >= 1


def test_home_html_escapes_username():
    """HTML metacharacters in username must be escaped in the home page."""
    page = _home_html("<b>hi</b>")
    assert "<b>hi</b>" not in page
    assert "&lt;b&gt;" in page


async def test_must_change_user_is_confined_to_set_password(client_and_pw):
    ac, pw = client_and_pw
    await ac.post("/login", data={"username": "antz", "password": pw})
    # gate bounces any other route back to /set-password
    r = await ac.get("/")
    assert r.status_code == 303
    assert r.headers["location"] == "/set-password"
    sp = await ac.get("/set-password")
    assert sp.status_code == 200
    assert "new password" in sp.text.lower()


async def test_set_password_requires_session(client_and_pw):
    ac, _ = client_and_pw
    r = await ac.get("/set-password")
    assert r.status_code == 303
    assert r.headers["location"] == "/login"
    r2 = await ac.post("/set-password", data={"new_password": "a-good-long-password"})
    assert r2.status_code == 303
    assert r2.headers["location"] == "/login"


async def test_set_password_rejects_short(client_and_pw):
    ac, pw = client_and_pw
    await ac.post("/login", data={"username": "antz", "password": pw})
    r = await ac.post("/set-password", data={"new_password": "short"})
    assert r.status_code == 400


async def test_set_password_short_keeps_user_confined(client_and_pw):
    ac, pw = client_and_pw
    await ac.post("/login", data={"username": "antz", "password": pw})
    r = await ac.post("/set-password", data={"new_password": "short"})
    assert r.status_code == 400
    assert "at least 8" in r.text.lower()
    # still must_change → any other route bounces back to /set-password
    home = await ac.get("/")
    assert home.status_code == 303
    assert home.headers["location"] == "/set-password"


async def test_set_password_clears_flag_and_unlocks_board(client_and_pw):
    ac, pw = client_and_pw
    await ac.post("/login", data={"username": "antz", "password": pw})
    r = await ac.post("/set-password", data={"new_password": "a-good-long-password"})
    assert r.status_code == 303
    assert r.headers["location"] == "/"
    home = await ac.get("/")
    assert home.status_code == 200
    assert "signed in as antz" in home.text
