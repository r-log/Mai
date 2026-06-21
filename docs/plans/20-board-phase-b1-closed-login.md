# Board Phase B1 — Closed Login + Account Provisioning — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the closed-access web layer: admin-provisioned accounts (no public sign-up), a username+password login that is the **sole gate** to everything, with argon2id password storage and a forced first-login password change.

**Architecture:** A new `mai.auth` package (argon2id `PasswordHasher` seam + an account-creation service), a `User` model + `UserRepository`, `mai user-add`/`user-list` CLI for provisioning, and a new `mai.web` FastAPI app whose session middleware rejects every request without a valid session (302 → `/login`) and confines freshly-provisioned accounts to `/set-password` until they pick their own password. This plan delivers the gate end-to-end; the board API (B2) and board UI (B3) mount their routes inside this same gated app in later plans.

**Tech Stack:** Python 3.12, FastAPI + Starlette `SessionMiddleware`, `argon2-cffi`, async SQLAlchemy 2.0, pytest + pytest-asyncio + httpx `ASGITransport`.

## Global Constraints

- **Python 3.12**, async SQLAlchemy 2.0, FastAPI/Starlette, pydantic. New runtime deps added in Task 1: `fastapi`, `uvicorn[standard]`, `argon2-cffi`, `python-multipart`, `itsdangerous`.
- **Closed access — login is the sole gate.** No registration/sign-up route exists. Every route except `/login`, `/logout`, and `/static/*` requires a valid session; a `must_change_password` session is confined to `/set-password`.
- **Passwords never stored or logged in clear.** Stored only as argon2id hashes. Login failures return a **generic** "Invalid username or password" (no user enumeration), HTTP 401.
- **Accounts are admin-provisioned** via CLI only; a fresh account has `must_change_password=True` and a generated one-time password printed **once**.
- 4-space indent, no tabs. `feat:`/`docs:`/`test:` commits, **NO AI attribution** (no `Co-Authored-By`, no "Generated with"). Commit with `git -c user.name="r-log" commit -m "..."`.
- Follow existing patterns: models in `src/mai/db/models.py` (`Mapped`/`mapped_column`, `_now`/`_uuid` helpers), repositories in `src/mai/repository/<name>.py` (constructor takes `session`), `Fake*` seams. Tests in `tests/`, run `python -m pytest`. The `session` fixture gives an in-memory async SQLite session with all tables created.
- The web app is built by a **factory** `create_app(session_factory, hasher, session_secret, *, cookie_secure=True)` so tests inject an in-memory factory + `FakeHasher` + `cookie_secure=False`. No global app state that blocks DI.

---

## File Structure

| File | Responsibility |
|------|----------------|
| `pyproject.toml` (modify) | Add the 5 runtime deps. |
| `src/mai/db/models.py` (modify) | Add the `User` model. |
| `src/mai/repository/users.py` | `UserRepository` (get/create/all/set_password). |
| `src/mai/auth/__init__.py` | Package marker. |
| `src/mai/auth/hasher.py` | `PasswordHasher` protocol + `Argon2Hasher`. |
| `src/mai/auth/fake.py` | `FakeHasher` (fast, deterministic — tests only). |
| `src/mai/auth/accounts.py` | `create_account()` service + `generate_password()`. |
| `src/mai/web/__init__.py` | Package marker. |
| `src/mai/web/app.py` | `create_app()` — session middleware gate + `/login` `/logout` `/set-password` `/` routes + tiny inline HTML. |
| `src/mai/web/asgi.py` | Production app builder from `settings` (for uvicorn). |
| `src/mai/config.py` (modify) | Add `session_secret`, `cookie_secure`. |
| `src/mai/cli/__main__.py` (modify) | Add `user-add`, `user-list`, `serve-web` commands. |
| `tests/test_user_repository.py` | UserRepository CRUD + uniqueness. |
| `tests/test_password_hasher.py` | Argon2 + Fake hash/verify roundtrip. |
| `tests/test_accounts.py` | `create_account` (must_change, returns verifying pw, duplicate raises); `generate_password`. |
| `tests/test_web_login.py` | gate redirect, login success/fail, must_change confinement, set-password, logout. |
| `tests/test_cli_parser_b1.py` | parser accepts `user-add`/`user-list`/`serve-web`. |

---

## Task 1: Deps + User model + UserRepository

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/mai/db/models.py`
- Create: `src/mai/repository/users.py`
- Test: `tests/test_user_repository.py`

**Interfaces:**
- Produces: `User` model (PK `username`; cols `password_hash, display_name, is_maintainer, must_change_password, created_at, last_login`); `UserRepository(session)` with `async get(username)->User|None`, `async create(username, password_hash, *, is_maintainer=False, display_name="")->User`, `async set_password(user, password_hash)->None`, `async all()->list[User]`.

- [ ] **Step 1: Add dependencies**

In `pyproject.toml`, replace the `dependencies = [...]` list so it reads:

```toml
dependencies = [
    "sqlalchemy>=2.0",
    "pydantic-settings>=2.0",
    "pydantic>=2.0",
    "aiosqlite>=0.19",
    "asyncpg>=0.29",
    "httpx>=0.27",
    "fastapi>=0.110",
    "uvicorn[standard]>=0.29",
    "argon2-cffi>=23.1",
    "python-multipart>=0.0.9",
    "itsdangerous>=2.1",
]
```

Then install: `python -m pip install -e .` (Expected: argon2-cffi, fastapi, uvicorn, python-multipart, itsdangerous resolve and install.)

- [ ] **Step 2: Write the failing test**

Create `tests/test_user_repository.py`:

```python
import pytest

from mai.repository.users import UserRepository


async def test_create_and_get(session):
    repo = UserRepository(session)
    await repo.create("antz", "hash1", is_maintainer=True)
    await session.commit()
    user = await repo.get("antz")
    assert user is not None
    assert user.username == "antz"
    assert user.password_hash == "hash1"
    assert user.is_maintainer is True
    assert user.must_change_password is True  # fresh accounts must change
    assert user.display_name == "antz"        # defaults to username


async def test_get_unknown_returns_none(session):
    assert await UserRepository(session).get("nobody") is None


async def test_set_password_clears_must_change(session):
    repo = UserRepository(session)
    user = await repo.create("madmax", "old")
    await session.commit()
    await repo.set_password(user, "new")
    await session.commit()
    refreshed = await repo.get("madmax")
    assert refreshed.password_hash == "new"
    assert refreshed.must_change_password is False


async def test_all_sorted_by_username(session):
    repo = UserRepository(session)
    await repo.create("zeb", "h")
    await repo.create("ana", "h")
    await session.commit()
    assert [u.username for u in await repo.all()] == ["ana", "zeb"]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_user_repository.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mai.repository.users'`.

- [ ] **Step 4: Add the User model**

In `src/mai/db/models.py`, add this class at the end of the file (the imports `Boolean`, `String`, `Text`, `Mapped`, `mapped_column`, `_now`, `datetime` are already present):

```python
class User(Base):
    """An admin-provisioned login account. No self-registration."""
    __tablename__ = "users"
    username: Mapped[str] = mapped_column(String(64), primary_key=True)
    password_hash: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    is_maintainer: Mapped[bool] = mapped_column(Boolean, default=False)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(default=_now)
    last_login: Mapped[datetime | None] = mapped_column(nullable=True)
```

- [ ] **Step 5: Write the UserRepository**

Create `src/mai/repository/users.py`:

```python
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mai.db.models import User


class UserRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get(self, username: str) -> User | None:
        return await self._session.scalar(
            select(User).where(User.username == username))

    async def create(self, username: str, password_hash: str, *,
                     is_maintainer: bool = False, display_name: str = "") -> User:
        user = User(username=username, password_hash=password_hash,
                    is_maintainer=is_maintainer,
                    display_name=display_name or username,
                    must_change_password=True)
        self._session.add(user)
        return user

    async def set_password(self, user: User, password_hash: str) -> None:
        user.password_hash = password_hash
        user.must_change_password = False

    async def all(self) -> list[User]:
        return list(await self._session.scalars(
            select(User).order_by(User.username)))
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_user_repository.py -v`
Expected: PASS (4 passed).

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src/mai/db/models.py src/mai/repository/users.py tests/test_user_repository.py
git -c user.name="r-log" commit -m "feat: User model + UserRepository + web/auth deps"
```

---

## Task 2: Password hasher seam + account service

**Files:**
- Create: `src/mai/auth/__init__.py` (empty)
- Create: `src/mai/auth/hasher.py`
- Create: `src/mai/auth/fake.py`
- Create: `src/mai/auth/accounts.py`
- Test: `tests/test_password_hasher.py`
- Test: `tests/test_accounts.py`

**Interfaces:**
- Consumes: `UserRepository` (Task 1).
- Produces: `PasswordHasher` protocol (`hash(password:str)->str`, `verify(password:str, hashed:str)->bool`); `Argon2Hasher`; `FakeHasher`; `generate_password()->str`; `async create_account(session, hasher, username, *, is_maintainer=False)->str` (returns the one-time plaintext password; raises `ValueError` if the username exists).

- [ ] **Step 1: Create the empty package marker**

Create `src/mai/auth/__init__.py` (empty file).

- [ ] **Step 2: Write the failing tests**

Create `tests/test_password_hasher.py`:

```python
import pytest

from mai.auth.fake import FakeHasher
from mai.auth.hasher import Argon2Hasher


def test_argon2_roundtrip():
    h = Argon2Hasher()
    hashed = h.hash("correct horse")
    assert hashed != "correct horse"          # never stored in clear
    assert h.verify("correct horse", hashed) is True
    assert h.verify("wrong", hashed) is False


def test_argon2_verify_rejects_garbage_hash():
    assert Argon2Hasher().verify("x", "not-a-valid-hash") is False


def test_fake_hasher_roundtrip():
    h = FakeHasher()
    hashed = h.hash("pw")
    assert h.verify("pw", hashed) is True
    assert h.verify("nope", hashed) is False
```

Create `tests/test_accounts.py`:

```python
import pytest

from mai.auth.accounts import create_account, generate_password
from mai.auth.fake import FakeHasher
from mai.repository.users import UserRepository


def test_generate_password_is_long_and_urlsafe():
    pw = generate_password()
    assert len(pw) >= 16
    assert pw.isascii() and " " not in pw


async def test_create_account_makes_must_change_user(session):
    hasher = FakeHasher()
    pw = await create_account(session, hasher, "antz", is_maintainer=True)
    await session.commit()
    user = await UserRepository(session).get("antz")
    assert user is not None
    assert user.is_maintainer is True
    assert user.must_change_password is True
    assert hasher.verify(pw, user.password_hash)   # returned pw matches stored hash
    assert pw not in user.password_hash            # stored value is not the plaintext


async def test_create_account_rejects_duplicate(session):
    hasher = FakeHasher()
    await create_account(session, hasher, "dup")
    await session.commit()
    with pytest.raises(ValueError):
        await create_account(session, hasher, "dup")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python -m pytest tests/test_password_hasher.py tests/test_accounts.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mai.auth.hasher'`.

- [ ] **Step 4: Write the hasher**

Create `src/mai/auth/hasher.py`:

```python
from typing import Protocol

from argon2 import PasswordHasher as _Argon2
from argon2.exceptions import InvalidHashError, VerifyMismatchError


class PasswordHasher(Protocol):
    def hash(self, password: str) -> str: ...
    def verify(self, password: str, hashed: str) -> bool: ...


class Argon2Hasher:
    """argon2id via argon2-cffi (library defaults are argon2id)."""

    def __init__(self) -> None:
        self._ph = _Argon2()

    def hash(self, password: str) -> str:
        return self._ph.hash(password)

    def verify(self, password: str, hashed: str) -> bool:
        try:
            return self._ph.verify(hashed, password)
        except (VerifyMismatchError, InvalidHashError):
            return False
```

Create `src/mai/auth/fake.py`:

```python
class FakeHasher:
    """Deterministic, fast, NOT secure. Tests only."""

    def hash(self, password: str) -> str:
        return f"fake$:{password}"

    def verify(self, password: str, hashed: str) -> bool:
        return hashed == f"fake$:{password}"
```

- [ ] **Step 5: Write the account service**

Create `src/mai/auth/accounts.py`:

```python
import secrets

from sqlalchemy.ext.asyncio import AsyncSession

from mai.auth.hasher import PasswordHasher
from mai.repository.users import UserRepository


def generate_password() -> str:
    """A one-time password for a freshly provisioned account."""
    return secrets.token_urlsafe(16)


async def create_account(session: AsyncSession, hasher: PasswordHasher,
                         username: str, *, is_maintainer: bool = False) -> str:
    """Create an account, returning the one-time plaintext password.

    Raises ValueError if the username already exists.
    """
    repo = UserRepository(session)
    if await repo.get(username) is not None:
        raise ValueError(f"user '{username}' already exists")
    password = generate_password()
    await repo.create(username, hasher.hash(password), is_maintainer=is_maintainer)
    return password
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_password_hasher.py tests/test_accounts.py -v`
Expected: PASS (6 passed).

- [ ] **Step 7: Commit**

```bash
git add src/mai/auth/__init__.py src/mai/auth/hasher.py src/mai/auth/fake.py src/mai/auth/accounts.py tests/test_password_hasher.py tests/test_accounts.py
git -c user.name="r-log" commit -m "feat: argon2id password hasher + account provisioning service"
```

---

## Task 3: FastAPI gated app — login + logout

**Files:**
- Create: `src/mai/web/__init__.py` (empty)
- Create: `src/mai/web/app.py`
- Modify: `src/mai/config.py`
- Test: `tests/test_web_login.py`

**Interfaces:**
- Consumes: `UserRepository` (Task 1), `PasswordHasher`/`FakeHasher` (Task 2).
- Produces: `create_app(session_factory, hasher, session_secret, *, cookie_secure=True) -> FastAPI` with a gate middleware + routes `GET /login`, `POST /login`, `POST /logout`, `GET /` (home placeholder). Session stores `username` + `must_change`. `settings.session_secret`, `settings.cookie_secure`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_web_login.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_web_login.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mai.web.app'`.

- [ ] **Step 3: Add config fields**

In `src/mai/config.py`, add inside `Settings` after `deploy_command: str | None = None`:

```python
    session_secret: str = "dev-insecure-change-me"
    cookie_secure: bool = True
```

- [ ] **Step 4: Write the app**

Create `src/mai/web/__init__.py` (empty file).

Create `src/mai/web/app.py`:

```python
from datetime import datetime, timezone

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from mai.repository.users import UserRepository

_PUBLIC = {"/login", "/logout"}


def _page(title: str, body: str) -> str:
    return f"<!doctype html><title>{title}</title><body>{body}</body>"


def _login_html(error: str = "") -> str:
    err = f"<p class='error'>{error}</p>" if error else ""
    return _page("Mai — Login", f"""
        <h1>Mai</h1>{err}
        <form method='post' action='/login'>
          <input name='username' placeholder='username' autofocus>
          <input name='password' type='password' placeholder='password'>
          <button type='submit'>Log in</button>
        </form>""")


def create_app(session_factory, hasher, session_secret: str, *,
               cookie_secure: bool = True) -> FastAPI:
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key=session_secret,
                       https_only=cookie_secure, same_site="lax")

    @app.middleware("http")
    async def gate(request: Request, call_next):
        path = request.url.path
        if path in _PUBLIC or path.startswith("/static"):
            return await call_next(request)
        if not request.session.get("username"):
            return RedirectResponse("/login", status_code=303)
        if request.session.get("must_change") and path != "/set-password":
            return RedirectResponse("/set-password", status_code=303)
        return await call_next(request)

    @app.get("/login", response_class=HTMLResponse)
    async def login_form() -> str:
        return _login_html()

    @app.post("/login")
    async def login(request: Request, username: str = Form(...),
                    password: str = Form(...)):
        async with session_factory() as session:
            user = await UserRepository(session).get(username)
            if user is None or not hasher.verify(password, user.password_hash):
                return HTMLResponse(
                    _login_html("Invalid username or password"), status_code=401)
            user.last_login = datetime.now(timezone.utc)
            await session.commit()
            must_change = user.must_change_password
        request.session["username"] = username
        request.session["must_change"] = must_change
        return RedirectResponse("/set-password" if must_change else "/",
                                status_code=303)

    @app.post("/logout")
    async def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request) -> str:
        return _page("Mai", f"<p>signed in as {request.session.get('username')}</p>"
                            "<form method='post' action='/logout'>"
                            "<button>Log out</button></form>")

    return app
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_web_login.py -v`
Expected: PASS (6 passed).

- [ ] **Step 6: Commit**

```bash
git add src/mai/web/__init__.py src/mai/web/app.py src/mai/config.py tests/test_web_login.py
git -c user.name="r-log" commit -m "feat: gated FastAPI app with login/logout (session is the gate)"
```

---

## Task 4: First-login password change

**Files:**
- Modify: `src/mai/web/app.py`
- Test: `tests/test_web_login.py` (add cases)

**Interfaces:**
- Consumes: the app from Task 3.
- Produces: `GET /set-password`, `POST /set-password` (clears `must_change_password` + session `must_change`); the gate already confines `must_change` sessions to `/set-password`.

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_web_login.py`:

```python
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


async def test_set_password_rejects_short(client_and_pw):
    ac, pw = client_and_pw
    await ac.post("/login", data={"username": "antz", "password": pw})
    r = await ac.post("/set-password", data={"new_password": "short"})
    assert r.status_code == 400


async def test_set_password_clears_flag_and_unlocks_board(client_and_pw):
    ac, pw = client_and_pw
    await ac.post("/login", data={"username": "antz", "password": pw})
    r = await ac.post("/set-password", data={"new_password": "a-good-long-password"})
    assert r.status_code == 303
    assert r.headers["location"] == "/"
    home = await ac.get("/")
    assert home.status_code == 200
    assert "signed in as antz" in home.text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_web_login.py -v`
Expected: the 3 new tests FAIL (404/405 on `/set-password`, no confinement target yet).

- [ ] **Step 3: Implement set-password**

In `src/mai/web/app.py`, add a `_set_password_html` helper next to `_login_html`:

```python
def _set_password_html(error: str = "") -> str:
    err = f"<p class='error'>{error}</p>" if error else ""
    return _page("Mai — Set password", f"""
        <h1>Set your password</h1>{err}
        <form method='post' action='/set-password'>
          <input name='new_password' type='password' placeholder='new password' autofocus>
          <button type='submit'>Save</button>
        </form>""")
```

Then add these two routes inside `create_app` (before `return app`):

```python
    @app.get("/set-password", response_class=HTMLResponse)
    async def set_password_form(request: Request) -> str:
        return _set_password_html()

    @app.post("/set-password")
    async def set_password(request: Request, new_password: str = Form(...)):
        if len(new_password) < 8:
            return HTMLResponse(
                _set_password_html("Password must be at least 8 characters"),
                status_code=400)
        username = request.session["username"]
        async with session_factory() as session:
            repo = UserRepository(session)
            user = await repo.get(username)
            await repo.set_password(user, hasher.hash(new_password))
            await session.commit()
        request.session["must_change"] = False
        return RedirectResponse("/", status_code=303)
```

(The gate already lets a `must_change` session reach `/set-password`; the home route `GET /` is reachable once `must_change` is cleared.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_web_login.py -v`
Expected: PASS (9 passed).

- [ ] **Step 5: Commit**

```bash
git add src/mai/web/app.py tests/test_web_login.py
git -c user.name="r-log" commit -m "feat: forced first-login password change"
```

---

## Task 5: Provisioning CLI + production app entry

**Files:**
- Create: `src/mai/web/asgi.py`
- Modify: `src/mai/cli/__main__.py`
- Test: `tests/test_cli_parser_b1.py`

**Interfaces:**
- Consumes: `create_account` (Task 2), `Argon2Hasher` (Task 2), `UserRepository` (Task 1), `create_app` (Task 3), `settings` (Tasks 3).
- Produces: `build_app() -> FastAPI` (from settings) in `mai.web.asgi`; CLI `user-add <username> [--maintainer]`, `user-list`, `serve-web`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli_parser_b1.py`:

```python
from mai.cli.__main__ import build_parser


def test_parser_accepts_user_add():
    args = build_parser().parse_args(["user-add", "antz", "--maintainer"])
    assert args.cmd == "user-add"
    assert args.username == "antz"
    assert args.maintainer is True


def test_parser_user_add_defaults_not_maintainer():
    args = build_parser().parse_args(["user-add", "madmax"])
    assert args.maintainer is False


def test_parser_accepts_user_list_and_serve_web():
    assert build_parser().parse_args(["user-list"]).cmd == "user-list"
    assert build_parser().parse_args(["serve-web"]).cmd == "serve-web"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli_parser_b1.py -v`
Expected: FAIL (argparse exits non-zero on the unknown `user-add` subcommand).

- [ ] **Step 3: Write the production app builder**

Create `src/mai/web/asgi.py`:

```python
from mai.auth.hasher import Argon2Hasher
from mai.config import settings
from mai.db.session import SessionFactory
from mai.web.app import create_app


def build_app():
    return create_app(SessionFactory, Argon2Hasher(), settings.session_secret,
                      cookie_secure=settings.cookie_secure)
```

- [ ] **Step 4: Add the CLI helpers**

In `src/mai/cli/__main__.py`, add these coroutines next to the other `_*` helpers:

```python
async def _user_add(username: str, is_maintainer: bool) -> str:
    from mai.auth.accounts import create_account
    from mai.auth.hasher import Argon2Hasher

    async with SessionFactory() as session:
        password = await create_account(session, Argon2Hasher(), username,
                                         is_maintainer=is_maintainer)
        await session.commit()
    return password


async def _user_list() -> list:
    from mai.repository.users import UserRepository

    async with SessionFactory() as session:
        return await UserRepository(session).all()
```

- [ ] **Step 5: Add the subparsers**

In `build_parser()`, add after the `serve` parser:

```python
    ua = sub.add_parser("user-add")
    ua.add_argument("username")
    ua.add_argument("--maintainer", action="store_true")
    sub.add_parser("user-list")
    sub.add_parser("serve-web")
```

- [ ] **Step 6: Wire the dispatch**

In `main()`, add these branches to the dispatch chain (after the `serve` branch):

```python
    elif args.cmd == "user-add":
        try:
            password = asyncio.run(_user_add(args.username, args.maintainer))
        except ValueError as exc:
            raise SystemExit(str(exc))
        print(f"created user '{args.username}'"
              f"{' (maintainer)' if args.maintainer else ''}")
        print(f"one-time password (give to the user privately, they must change it "
              f"on first login):\n    {password}")
    elif args.cmd == "user-list":
        users = asyncio.run(_user_list())
        for u in users:
            flags = []
            if u.is_maintainer:
                flags.append("maintainer")
            if u.must_change_password:
                flags.append("must-change-pw")
            suffix = f"  [{', '.join(flags)}]" if flags else ""
            print(f"{u.username}{suffix}")
        print(f"{len(users)} user(s)")
    elif args.cmd == "serve-web":
        import uvicorn

        from mai.web.asgi import build_app
        print("serving web app on http://127.0.0.1:8000 (Ctrl-C to stop)")
        uvicorn.run(build_app(), host="127.0.0.1", port=8000)
```

- [ ] **Step 7: Run the parser test + full suite**

Run: `python -m pytest tests/test_cli_parser_b1.py -v`
Expected: PASS (3 passed).
Run: `python -m pytest -q`
Expected: all green (185 prior + the new B1 tests).

- [ ] **Step 8: Smoke the provisioning + gate locally**

```bash
python -m mai.cli init-db
python -m mai.cli user-add tester
python -m mai.cli user-list
```
Expected: `user-add` prints a one-time password; `user-list` shows `tester  [must-change-pw]`. (Do NOT start `serve-web` in the smoke unless you can Ctrl-C it; note in the report that it launches uvicorn.)

- [ ] **Step 9: Commit**

```bash
git add src/mai/web/asgi.py src/mai/cli/__main__.py tests/test_cli_parser_b1.py
git -c user.name="r-log" commit -m "feat: user-add/user-list/serve-web CLI"
```

---

## Self-Review

**Spec coverage (`port-debt-board-multiuser.md`, the B1 slice of Phase B):**
- "username + password login … argon2id" → Tasks 2–3 (`Argon2Hasher`, `/login`).
- "admin-provisioned accounts, no registration" → Task 2 `create_account` + Task 5 `user-add`; no registration route exists anywhere.
- "login is the sole gate; no anonymous read/write" → Task 3 gate middleware (every path except `/login`/`/logout`/`/static`).
- "first-login forced password change (`must_change_password`)" → Tasks 1 (column default True), 3 (session flag + confinement), 4 (`/set-password` clears it).
- "passwords never in clear; generic login failure (no enumeration)" → Task 2 (hash only) + Task 3 (generic 401 for both wrong-pw and unknown-user).
- "`is_maintainer` set at creation" → Task 1 col + Task 2/5 `--maintainer`.
- "`User` table; `UserRepository`" → Task 1.
- CLI `mai user-add`/`user-list` → Task 5.

**Deferred to B2/B3 (not gaps):** `BoardItem`/`BoardEvent` + board API (B2); `/port/` UI served behind the gate + hydration + toggles (B3). The home route here is a placeholder the board UI replaces.

**Placeholder scan:** none — every code step is complete. `serve-web` launches uvicorn (runtime, not unit-tested); its parser is tested and the app it serves is fully tested via `create_app`.

**Type consistency:** `create_app(session_factory, hasher, session_secret, *, cookie_secure=True)` is used identically in `tests/test_web_login.py` and `mai/web/asgi.py`. `create_account(session, hasher, username, *, is_maintainer=False) -> str` matches across Task 2 tests and Task 5 CLI. `UserRepository` methods (`get`, `create`, `set_password`, `all`) are consistent across Tasks 1, 2, 3, 5. Session keys `username`/`must_change` are written in `/login` and read in the gate + `/set-password` consistently.

---

## Execution Handoff

After saving, choose execution mode:
1. **Subagent-Driven (recommended)** — a fresh subagent per task, two-stage review between tasks.
2. **Inline Execution** — execute in this session with checkpoints.
