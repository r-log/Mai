# Board Phase B2 — Board State + API — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the gated app shared, audited board state — every signed-in user sees the same assignments/statuses, can self-claim, maintainers can assign/dismiss — and keep it truthful: a card auto-archives when the engine confirms the fix landed.

**Architecture:** A new `BoardItem` (durable human intent, keyed on the engine's `port_candidate_id = f"{patch_group_id}:{target_core}"`) + append-only `BoardEvent` audit, with repositories and a `board` service that mutates an item and records an event atomically. A FastAPI `APIRouter` (mounted inside B1's gated `create_app`) serves `GET /api/board` (the engine's `build_port_candidates` overlaid with live `BoardItem` state) and the `POST /api/board/{id}/{action}` mutations, behind the session gate + a CSRF token. The refresh cycle gains a reconciliation step that archives a `BoardItem` once its candidate is no longer open. Engine owns truth; the board owns intent; a click can never mark a fix "ported".

**Tech Stack:** Python 3.12, FastAPI/Starlette, async SQLAlchemy 2.0, pytest + httpx `ASGITransport`.

## Global Constraints

- **Engine owns truth; board owns intent.** No board route sets a `PortCandidate` to `ported`/`dismissed`. Human "dismiss" lives on `BoardItem`, not the engine row.
- **Login is the gate (from B1).** `/api/board*` is NOT public; the existing gate middleware already 302s unauthenticated requests to `/login`. Mutations additionally require a **CSRF token** (`X-CSRF-Token` header matching the session token).
- **Authorization:** any logged-in user may `claim`/`status`/`link-pr` (and claim/relinquish their own); **assign (others), dismiss, restore** require `is_maintainer`. Enforced server-side.
- **Audit everything:** every successful mutation appends a `BoardEvent` (append-only; never updated/deleted).
- **Stable key:** `BoardItem.port_candidate_id == f"{patch_group_id}:{target_core}"`, identical to the v1 card id and the `build_port_candidates` output `id`.
- 4-space indent, no tabs. `feat:`/`test:`/`fix:` commits, **NO AI attribution**. Commit with `git -c user.name="r-log" commit -m "..."`.
- Follow existing patterns: models in `src/mai/db/models.py`; repositories `src/mai/repository/<name>.py` (ctor takes `session`); tests in `tests/`, run `python -m pytest`. The `session` fixture gives an in-memory async session with all tables. Web tests build the app via `create_app(session_factory, hasher, "test-secret", cookie_secure=False)` (B1 pattern) and drive it with `httpx.ASGITransport`.

---

## File Structure

| File | Responsibility |
|------|----------------|
| `src/mai/db/models.py` (modify) | Add `BoardItem`, `BoardEvent`. |
| `src/mai/repository/board.py` | `BoardItemRepository`, `BoardEventRepository`. |
| `src/mai/board/__init__.py` | Package marker. |
| `src/mai/board/service.py` | `apply_action()` (mutate item + append event), `ClaimConflict`, `ACTIONS`. |
| `src/mai/web/board_api.py` | `make_board_router(session_factory)` — `GET /api/board` + `POST /api/board/{id}/{action}` + CSRF helpers. |
| `src/mai/web/app.py` (modify) | Mount the board router; issue a CSRF token into the session. |
| `src/mai/refresh/cycle.py` (modify) | Reconcile: archive `BoardItem`s whose candidate is no longer open. |
| `tests/test_board_models.py` | repositories CRUD + event seq. |
| `tests/test_board_service.py` | each action + claim conflict + audit trail + role-agnostic core logic. |
| `tests/test_board_api.py` | GET overlay, POST mutations, role enforcement, CSRF, concurrency. |
| `tests/test_board_reconcile.py` | archive-on-resolve in the cycle. |

---

## Task 1: BoardItem + BoardEvent models + repositories

**Files:**
- Modify: `src/mai/db/models.py`
- Create: `src/mai/repository/board.py`
- Test: `tests/test_board_models.py`

**Interfaces:**
- Produces: `BoardItem` (PK `port_candidate_id`; cols `assignee, status, related_pr, dismiss_reason, archived, updated_by, updated_at`); `BoardEvent` (`id, port_candidate_id, seq, actor, action, from_value, to_value, at`); `BoardItemRepository(session)` with `get(pcid)`, `get_or_create(pcid, actor)`, `active()`; `BoardEventRepository(session)` with `next_seq(pcid)`, `append(pcid, actor, action, from_value, to_value)`, `for_item(pcid)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_board_models.py`:

```python
from mai.repository.board import BoardEventRepository, BoardItemRepository


async def test_get_or_create_is_idempotent(session):
    repo = BoardItemRepository(session)
    a = await repo.get_or_create("pg1:three", "antz")
    await session.commit()
    b = await repo.get_or_create("pg1:three", "madmax")
    await session.commit()
    assert a.port_candidate_id == b.port_candidate_id
    assert (await repo.get("pg1:three")).status == "open"


async def test_active_excludes_archived(session):
    repo = BoardItemRepository(session)
    keep = await repo.get_or_create("pg1:three", "antz")
    gone = await repo.get_or_create("pg2:two", "antz")
    gone.archived = True
    await session.commit()
    ids = [bi.port_candidate_id for bi in await repo.active()]
    assert "pg1:three" in ids
    assert "pg2:two" not in ids


async def test_event_seq_increments_per_item(session):
    events = BoardEventRepository(session)
    await events.append("pg1:three", "antz", "claim", None, "antz")
    await events.append("pg1:three", "antz", "status", "claimed", "in_progress")
    await events.append("pg2:two", "antz", "claim", None, "antz")
    await session.commit()
    seqs = [e.seq for e in await events.for_item("pg1:three")]
    assert seqs == [1, 2]
    assert [e.seq for e in await events.for_item("pg2:two")] == [1]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_board_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mai.repository.board'`.

- [ ] **Step 3: Add the models**

In `src/mai/db/models.py`, add at the end (existing imports cover `Boolean, Integer, String, Text, UniqueConstraint, Mapped, mapped_column, _now, _uuid, datetime`):

```python
class BoardItem(Base):
    """Durable human intent over one PortCandidate. Engine truth lives elsewhere."""
    __tablename__ = "board_item"
    port_candidate_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    assignee: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="open")
    related_pr: Mapped[str | None] = mapped_column(Text, nullable=True)
    dismiss_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_by: Mapped[str] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)


class BoardEvent(Base):
    """Append-only audit of board mutations."""
    __tablename__ = "board_event"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    port_candidate_id: Mapped[str] = mapped_column(String(128), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(24))
    from_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    to_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    at: Mapped[datetime] = mapped_column(default=_now)

    __table_args__ = (
        UniqueConstraint("port_candidate_id", "seq", name="uq_board_event_seq"),
    )
```

- [ ] **Step 4: Write the repositories**

Create `src/mai/repository/board.py`:

```python
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mai.db.models import BoardEvent, BoardItem


class BoardItemRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get(self, pcid: str) -> BoardItem | None:
        return await self._session.scalar(
            select(BoardItem).where(BoardItem.port_candidate_id == pcid))

    async def get_or_create(self, pcid: str, actor: str) -> BoardItem:
        existing = await self.get(pcid)
        if existing is not None:
            return existing
        item = BoardItem(port_candidate_id=pcid, status="open", updated_by=actor)
        self._session.add(item)
        return item

    async def active(self) -> list[BoardItem]:
        return list(await self._session.scalars(
            select(BoardItem).where(BoardItem.archived.is_(False))
            .order_by(BoardItem.port_candidate_id)))


class BoardEventRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def next_seq(self, pcid: str) -> int:
        current = await self._session.scalar(
            select(func.max(BoardEvent.seq))
            .where(BoardEvent.port_candidate_id == pcid))
        return (current or 0) + 1

    async def append(self, pcid: str, actor: str, action: str,
                     from_value: str | None, to_value: str | None) -> BoardEvent:
        event = BoardEvent(port_candidate_id=pcid, seq=await self.next_seq(pcid),
                           actor=actor, action=action,
                           from_value=from_value, to_value=to_value)
        self._session.add(event)
        return event

    async def for_item(self, pcid: str) -> list[BoardEvent]:
        return list(await self._session.scalars(
            select(BoardEvent).where(BoardEvent.port_candidate_id == pcid)
            .order_by(BoardEvent.seq)))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_board_models.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add src/mai/db/models.py src/mai/repository/board.py tests/test_board_models.py
git -c user.name="r-log" commit -m "feat: BoardItem + BoardEvent models and repositories"
```

---

## Task 2: Board service (mutations + audit)

**Files:**
- Create: `src/mai/board/__init__.py` (empty)
- Create: `src/mai/board/service.py`
- Test: `tests/test_board_service.py`

**Interfaces:**
- Consumes: `BoardItemRepository`, `BoardEventRepository` (Task 1).
- Produces: `class ClaimConflict(Exception)`; `ACTIONS = {"claim","assign","unassign","status","link_pr","dismiss","restore"}`; `VALID_STATUS = {"open","claimed","in_progress","pr_linked","dismissed"}`; `async apply_action(session, *, item_id, actor, action, value=None, reason=None, related_pr=None) -> BoardItem`. Raises `ValueError` for unknown action/invalid status, `ClaimConflict` if claiming a card already assigned to someone else.

- [ ] **Step 1: Write the failing test**

Create `tests/test_board_service.py`:

```python
import pytest

from mai.board.service import ClaimConflict, apply_action
from mai.repository.board import BoardEventRepository, BoardItemRepository


async def test_claim_sets_assignee_and_status_and_audits(session):
    item = await apply_action(session, item_id="pg1:three", actor="antz", action="claim")
    await session.commit()
    assert item.assignee == "antz"
    assert item.status == "claimed"
    events = await BoardEventRepository(session).for_item("pg1:three")
    assert [(e.action, e.to_value) for e in events] == [("claim", "antz")]


async def test_claim_conflict_when_someone_else_holds_it(session):
    await apply_action(session, item_id="pg1:three", actor="antz", action="claim")
    await session.commit()
    with pytest.raises(ClaimConflict):
        await apply_action(session, item_id="pg1:three", actor="madmax", action="claim")


async def test_reclaim_by_same_user_is_noop_ok(session):
    await apply_action(session, item_id="pg1:three", actor="antz", action="claim")
    await session.commit()
    item = await apply_action(session, item_id="pg1:three", actor="antz", action="claim")
    await session.commit()
    assert item.assignee == "antz"


async def test_assign_sets_other_user(session):
    item = await apply_action(session, item_id="pg1:three", actor="r-log",
                              action="assign", value="madmax")
    await session.commit()
    assert item.assignee == "madmax"
    assert item.status == "claimed"


async def test_unassign_clears(session):
    await apply_action(session, item_id="pg1:three", actor="antz", action="claim")
    item = await apply_action(session, item_id="pg1:three", actor="antz", action="unassign")
    await session.commit()
    assert item.assignee is None
    assert item.status == "open"


async def test_status_transition_validates(session):
    item = await apply_action(session, item_id="pg1:three", actor="antz",
                              action="status", value="in_progress")
    await session.commit()
    assert item.status == "in_progress"
    with pytest.raises(ValueError):
        await apply_action(session, item_id="pg1:three", actor="antz",
                           action="status", value="bogus")


async def test_link_pr_sets_status_pr_linked(session):
    item = await apply_action(session, item_id="pg1:three", actor="antz",
                              action="link_pr", related_pr="https://github.com/x/y/pull/1")
    await session.commit()
    assert item.related_pr.endswith("/pull/1")
    assert item.status == "pr_linked"


async def test_dismiss_requires_reason_then_restore(session):
    with pytest.raises(ValueError):
        await apply_action(session, item_id="pg1:three", actor="r-log", action="dismiss")
    item = await apply_action(session, item_id="pg1:three", actor="r-log",
                              action="dismiss", reason="WotLK-only, N/A")
    await session.commit()
    assert item.status == "dismissed"
    assert item.dismiss_reason == "WotLK-only, N/A"
    restored = await apply_action(session, item_id="pg1:three", actor="r-log",
                                  action="restore")
    await session.commit()
    assert restored.status == "open"
    assert restored.dismiss_reason is None


async def test_unknown_action_raises(session):
    with pytest.raises(ValueError):
        await apply_action(session, item_id="pg1:three", actor="antz", action="frobnicate")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_board_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mai.board.service'`.

- [ ] **Step 3: Write the service**

Create `src/mai/board/__init__.py` (empty file).

Create `src/mai/board/service.py`:

```python
from sqlalchemy.ext.asyncio import AsyncSession

from mai.repository.board import BoardEventRepository, BoardItemRepository

ACTIONS = {"claim", "assign", "unassign", "status", "link_pr", "dismiss", "restore"}
VALID_STATUS = {"open", "claimed", "in_progress", "pr_linked", "dismissed"}


class ClaimConflict(Exception):
    """Raised when claiming a card already assigned to a different user."""


async def apply_action(session: AsyncSession, *, item_id: str, actor: str,
                       action: str, value: str | None = None,
                       reason: str | None = None,
                       related_pr: str | None = None):
    if action not in ACTIONS:
        raise ValueError(f"unknown action: {action}")

    items = BoardItemRepository(session)
    events = BoardEventRepository(session)
    item = await items.get_or_create(item_id, actor)

    if action == "claim":
        if item.assignee and item.assignee != actor:
            raise ClaimConflict(item.assignee)
        from_, item.assignee, item.status = item.assignee, actor, "claimed"
        await events.append(item_id, actor, "claim", from_, actor)

    elif action == "assign":
        if not value:
            raise ValueError("assign requires a target username")
        from_, item.assignee, item.status = item.assignee, value, "claimed"
        await events.append(item_id, actor, "assign", from_, value)

    elif action == "unassign":
        from_, item.assignee, item.status = item.assignee, None, "open"
        await events.append(item_id, actor, "unassign", from_, None)

    elif action == "status":
        if value not in VALID_STATUS:
            raise ValueError(f"invalid status: {value}")
        from_, item.status = item.status, value
        await events.append(item_id, actor, "status", from_, value)

    elif action == "link_pr":
        if not related_pr:
            raise ValueError("link_pr requires related_pr")
        from_, item.related_pr, item.status = item.related_pr, related_pr, "pr_linked"
        await events.append(item_id, actor, "link_pr", from_, related_pr)

    elif action == "dismiss":
        if not reason:
            raise ValueError("dismiss requires a reason")
        from_, item.status, item.dismiss_reason = item.status, "dismissed", reason
        await events.append(item_id, actor, "dismiss", from_, reason)

    elif action == "restore":
        from_, item.status, item.dismiss_reason = item.status, "open", None
        await events.append(item_id, actor, "restore", from_, "open")

    item.updated_by = actor
    return item
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_board_service.py -v`
Expected: PASS (9 passed).

- [ ] **Step 5: Commit**

```bash
git add src/mai/board/__init__.py src/mai/board/service.py tests/test_board_service.py
git -c user.name="r-log" commit -m "feat: board mutation service with audit + claim conflict"
```

---

## Task 3: GET /api/board overlay + CSRF token + mount

**Files:**
- Create: `src/mai/web/board_api.py`
- Modify: `src/mai/web/app.py`
- Test: `tests/test_board_api.py`

**Interfaces:**
- Consumes: `build_port_candidates` (`mai.publish.dataviz`), `BoardItemRepository` (Task 1), `UserRepository` (B1).
- Produces: `make_board_router(session_factory) -> APIRouter`; `ensure_csrf(request) -> str` (stores/returns `request.session["csrf"]`); `GET /api/board` returns `{summary, columns, csrf, me:{username,is_maintainer}}` where each candidate gains a `board` key (null or `{assignee,status,related_pr,dismissed,dismiss_reason}`). `create_app` mounts the router and the home page issues the CSRF token.

- [ ] **Step 1: Write the failing test**

Create `tests/test_board_api.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_board_api.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mai.web.board_api'`.

- [ ] **Step 3: Write the router**

Create `src/mai/web/board_api.py`:

```python
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
```

- [ ] **Step 4: Mount the router in create_app**

In `src/mai/web/app.py`, add the import near the top (after the existing imports):

```python
from mai.web.board_api import make_board_router
```

Then inside `create_app`, immediately before `return app`, add:

```python
    app.include_router(make_board_router(session_factory))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_board_api.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Run the full suite**

Run: `python -m pytest -q`
Expected: all green (211 prior + new).

- [ ] **Step 7: Commit**

```bash
git add src/mai/web/board_api.py src/mai/web/app.py tests/test_board_api.py
git -c user.name="r-log" commit -m "feat: GET /api/board overlay + CSRF token + router mount"
```

---

## Task 4: POST mutations + role enforcement + CSRF

**Files:**
- Modify: `src/mai/web/board_api.py`
- Test: `tests/test_board_api.py` (add cases)

**Interfaces:**
- Consumes: `apply_action`, `ClaimConflict` (Task 2); `ensure_csrf` (Task 3); `UserRepository` (B1).
- Produces: `POST /api/board/{item_id}/{action}` (JSON body `{value?, reason?, related_pr?, csrf}`); returns the updated overlay or an error status. `claim`/`status`/`link_pr`/`unassign` allowed for any user; `assign`/`dismiss`/`restore` require maintainer (403 otherwise). Missing/blank CSRF → 403. `ClaimConflict` → 409. `ValueError` → 400.

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_board_api.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_board_api.py -v`
Expected: the 6 new tests FAIL (404/405 — no POST route yet).

- [ ] **Step 3: Implement the POST route**

In `src/mai/web/board_api.py`, add these imports at the top:

```python
from fastapi import HTTPException
from fastapi.responses import JSONResponse

from mai.board.service import ClaimConflict, apply_action
```

Then inside `make_board_router`, before `return router`, add:

```python
    _MAINTAINER_ONLY = {"assign", "dismiss", "restore"}

    @router.post("/{item_id}/{action}")
    async def mutate(request: Request, item_id: str, action: str):
        body = await request.json() if await request.body() else {}
        if not body.get("csrf") or body["csrf"] != request.session.get("csrf"):
            raise HTTPException(status_code=403, detail="bad csrf")
        username = request.session["username"]
        async with session_factory() as session:
            user = await UserRepository(session).get(username)
            is_maintainer = bool(user and user.is_maintainer)
            if action in _MAINTAINER_ONLY and not is_maintainer:
                raise HTTPException(status_code=403, detail="maintainer only")
            try:
                item = await apply_action(
                    session, item_id=item_id, actor=username, action=action,
                    value=body.get("value"), reason=body.get("reason"),
                    related_pr=body.get("related_pr"))
                await session.commit()
            except ClaimConflict as exc:
                return JSONResponse({"error": "already claimed",
                                     "assignee": str(exc)}, status_code=409)
            except ValueError as exc:
                return JSONResponse({"error": str(exc)}, status_code=400)
            return _overlay(item)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_board_api.py -v`
Expected: PASS (9 passed).

- [ ] **Step 5: Commit**

```bash
git add src/mai/web/board_api.py tests/test_board_api.py
git -c user.name="r-log" commit -m "feat: board mutation API with role + CSRF enforcement"
```

---

## Task 5: Refresh-cycle reconciliation (archive on resolve)

**Files:**
- Modify: `src/mai/refresh/cycle.py`
- Test: `tests/test_board_reconcile.py`

**Interfaces:**
- Consumes: `BoardItemRepository` (Task 1), `build_port_candidates` (the set of currently-open candidate ids).
- Produces: `async reconcile_board(session) -> int` (archives active `BoardItem`s whose id is not in the current open-candidate set; returns count archived); called near the end of `run_refresh_cycle` after `sync-analyze`, before `publish`. `RefreshResult` gains `archived_board_items: int`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_board_reconcile.py`:

```python
from mai.board.service import apply_action
from mai.refresh.cycle import reconcile_board
from mai.repository.board import BoardItemRepository


async def test_reconcile_archives_items_without_open_candidate(session):
    # no PortCandidate rows exist → every active board item is now resolved
    await apply_action(session, item_id="gone:three", actor="antz", action="claim")
    await session.commit()
    archived = await reconcile_board(session)
    await session.commit()
    assert archived == 1
    item = await BoardItemRepository(session).get("gone:three")
    assert item.archived is True


async def test_reconcile_keeps_items_with_open_candidate(session, monkeypatch):
    import mai.refresh.cycle as cycle
    await apply_action(session, item_id="live:three", actor="antz", action="claim")
    await session.commit()

    async def fake_build(_session):
        return {"columns": [{"candidates": [{"id": "live:three"}]}]}

    monkeypatch.setattr(cycle, "build_port_candidates", fake_build)
    archived = await reconcile_board(session)
    assert archived == 0
    assert (await BoardItemRepository(session).get("live:three")).archived is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_board_reconcile.py -v`
Expected: FAIL with `ImportError: cannot import name 'reconcile_board'`.

- [ ] **Step 3: Implement reconcile_board and wire it into the cycle**

In `src/mai/refresh/cycle.py`, add this import at the top of the module (module-level, so the test's `monkeypatch.setattr(cycle, "build_port_candidates", ...)` works):

```python
from mai.publish.dataviz import build_port_candidates
```

Add this function at module scope:

```python
async def reconcile_board(session) -> int:
    """Archive board items whose candidate is no longer open (engine resolved it)."""
    from mai.repository.board import BoardItemRepository

    board = await build_port_candidates(session)
    open_ids = {c["id"] for col in board["columns"] for c in col["candidates"]}
    repo = BoardItemRepository(session)
    archived = 0
    for item in await repo.active():
        if item.port_candidate_id not in open_ids:
            item.archived = True
            archived += 1
    return archived
```

In `RefreshResult`, add the field:

```python
    archived_board_items: int
```

In `run_refresh_cycle`, after the `compute_port_candidates`/sync block and its `await session.commit()`, before `publish_site`, add:

```python
    archived = await reconcile_board(session)
    await session.commit()
```

and include it in the returned `RefreshResult(...)`:

```python
        archived_board_items=archived,
```

(Update the existing `tests/test_refresh_cycle.py` expectations only if they construct/inspect `RefreshResult` positionally — they use keyword field access, so adding a field is safe. If any test fails because it builds `RefreshResult(...)` without the new field, add `archived_board_items=0` there.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_board_reconcile.py tests/test_refresh_cycle.py -v`
Expected: PASS (the 2 new + the existing 3 cycle tests).

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -q`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/mai/refresh/cycle.py tests/test_board_reconcile.py
git -c user.name="r-log" commit -m "feat: archive board items when the engine resolves their candidate"
```

---

## Self-Review

**Spec coverage (`port-debt-board-multiuser.md`, the B2 slice):**
- `BoardItem` + `BoardEvent` (§6.2) → Task 1.
- "claim/assign/status/link-pr/dismiss/restore" service + audit (§7) → Task 2.
- `GET /api/board` overlay + "requires a valid session" (§7) + CSRF (§10) → Task 3.
- POST mutations + role enforcement (any-user vs maintainer) + "already claimed by @x" 409 (§7, §11.5) → Task 4.
- "Reconcile board: archive BoardItem when candidate is `ported`/no longer open" (§7 step 3, §Invariant 6) → Task 5.
- Stable key `patch_group_id:target_core` (§Invariant 5) → Task 1 + reuse of `build_port_candidates` id.

**Deferred to B3 (not gaps):** the `/port/` UI (columns, toggles, assign/claim controls, evidence/history view) that consumes this API. The home `/` placeholder stays until B3.

**Carried security note (from B1 final review):** CSRF is now implemented here (Tasks 3–4). The session-secret-default deploy guard remains a deploy-time item (Phase A/C infra), not B2.

**Placeholder scan:** none — every step has complete code. The `_orphans` key in `GET /api/board` exists so board items for ids not present in the current engine snapshot (e.g. test fixtures, or a candidate mid-reconcile) are still observable; real candidates carry their overlay inline on `candidate["board"]`.

**Type consistency:** `apply_action(session, *, item_id, actor, action, value=None, reason=None, related_pr=None)` is identical across Task 2 tests, Task 4 route, and Task 5 is independent. `BoardItemRepository.active()`/`get()`/`get_or_create()` consistent across Tasks 1, 3, 5. `ensure_csrf`/`_overlay` defined in Task 3 and reused in Task 4. `make_board_router(session_factory)` mounted in Task 3.

---

## Execution Handoff

After saving, choose execution mode:
1. **Subagent-Driven (recommended)** — a fresh subagent per task, two-stage review between tasks.
2. **Inline Execution** — execute in this session with checkpoints.
