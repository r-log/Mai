from datetime import datetime, timezone

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mai.auth.accounts import create_account
from mai.auth.fake import FakeHasher
from mai.db.base import Base
from mai.db.models import Commit, CommitFile, PatchGroup, PortVerdict
from mai.git.fake import FakeGitClient
from mai.web.app import create_app

PATCH = (
    "diff --git a/src/shared/Db.cpp b/src/shared/Db.cpp\n"
    "--- a/src/shared/Db.cpp\n+++ b/src/shared/Db.cpp\n"
    "@@ -1,3 +1,4 @@\n a\n b\n+x\n c\n"
)
REJ = ""
ITEM_ID = "pg1:four"


@pytest_asyncio.fixture
async def env():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    hasher = FakeHasher()
    pws = {}
    async with factory() as s:
        pws["dev"] = await create_account(s, hasher, "dev", is_maintainer=False)
        ts = datetime(2026, 6, 1, tzinfo=timezone.utc)
        s.add(PatchGroup(id="pg1", patch_id="p1"))
        cm = Commit(
            core="three", sha="sha123abcd", author="a", authored_at=ts,
            committer="a", committed_at=ts,
            message="db crash fix on shutdown\n\nFrees the cache first.",
        )
        s.add(cm)
        await s.flush()
        s.add(CommitFile(
            commit_id=cm.id, path="src/shared/Db.cpp", subsystem="src/shared",
            change_type="M", added_lines=2, removed_lines=1,
        ))
        s.add(PortVerdict(
            patch_group_id="pg1", core="four", verdict="review",
            apply_result="conflict", relevance="portable", source_core="three",
            source_sha="sha123abcd", subsystem="src/shared", magnitude=3,
            tier="surgical", conflict_applied=1, conflict_total=1,
        ))
        await s.commit()

    git = FakeGitClient()
    git._diffs = {("three", "sha123abcd"): PATCH}
    git._rejected = {("four", PATCH): {}}
    git._logs = {"four": []}

    app = create_app(factory, hasher, "test-secret", cookie_secure=False,
                     review_git_client=git)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test",
                           follow_redirects=False) as ac:
        yield ac, pws


async def _login(ac, username, pw):
    await ac.post("/login", data={"username": username, "password": pw})
    await ac.post("/set-password", data={"new_password": "a-good-long-password"})


async def test_review_requires_session(env):
    ac, _ = env
    r = await ac.get(f"/api/review/{ITEM_ID}")
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


async def test_review_returns_evidence_for_review_item(env):
    ac, pws = env
    await _login(ac, "dev", pws["dev"])
    r = await ac.get(f"/api/review/{ITEM_ID}")
    assert r.status_code == 200
    body = r.json()
    assert "evidence" in body
    # review verdict exists — evidence should be a non-null object
    assert body["evidence"] is not None
    assert body["evidence"]["item_id"] == ITEM_ID


async def test_review_returns_null_for_non_review_item(env):
    ac, pws = env
    await _login(ac, "dev", pws["dev"])
    # Use an item_id that does not exist as a review verdict
    r = await ac.get("/api/review/pg1:two")
    assert r.status_code == 200
    body = r.json()
    assert body["evidence"] is None


@pytest_asyncio.fixture
async def client_factory():
    """Factory fixture — creates a logged-in ASGI client with configurable review_judge."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    hasher = FakeHasher()
    pws = {}
    async with factory() as s:
        pws["dev"] = await create_account(s, hasher, "dev", is_maintainer=False)
        ts = datetime(2026, 6, 1, tzinfo=timezone.utc)
        s.add(PatchGroup(id="pg1", patch_id="p1"))
        cm = Commit(
            core="three", sha="sha123abcd", author="a", authored_at=ts,
            committer="a", committed_at=ts,
            message="db crash fix on shutdown\n\nFrees the cache first.",
        )
        s.add(cm)
        await s.flush()
        s.add(CommitFile(
            commit_id=cm.id, path="src/shared/Db.cpp", subsystem="src/shared",
            change_type="M", added_lines=2, removed_lines=1,
        ))
        s.add(PortVerdict(
            patch_group_id="pg1", core="four", verdict="review",
            apply_result="conflict", relevance="portable", source_core="three",
            source_sha="sha123abcd", subsystem="src/shared", magnitude=3,
            tier="surgical", conflict_applied=1, conflict_total=1,
        ))
        await s.commit()

    git = FakeGitClient()
    git._diffs = {("three", "sha123abcd"): PATCH}
    git._rejected = {("four", PATCH): {}}
    git._logs = {"four": []}

    _clients = []

    async def _make(*, review_judge=None):
        app = create_app(factory, hasher, "test-secret", cookie_secure=False,
                         review_git_client=git, review_judge=review_judge)
        transport = ASGITransport(app=app)
        ac = AsyncClient(transport=transport, base_url="http://test",
                         follow_redirects=False)
        await ac.__aenter__()
        _clients.append(ac)
        await _login(ac, "dev", pws["dev"])
        return ac

    yield _make

    for ac in _clients:
        try:
            await ac.__aexit__(None, None, None)
        except Exception:
            pass


async def test_review_api_includes_opinion_when_judge_injected(client_factory):
    from mai.judge.fake import FakeJudge
    from mai.judge.schema import ReviewOpinion
    judge = FakeJudge(opinion=ReviewOpinion(assessment="divergent", confidence=0.4,
                                            reason="differs"))
    app_client = await client_factory(review_judge=judge)
    resp = await app_client.get("/api/review/pg1:four")
    assert resp.status_code == 200
    body = resp.json()
    assert "evidence" in body and "opinion" in body
    assert body["opinion"]["assessment"] == "divergent"


async def test_review_api_opinion_null_without_judge(client_factory):
    app_client = await client_factory(review_judge=None)
    resp = await app_client.get("/api/review/pg1:four")
    assert resp.status_code == 200
    assert resp.json()["opinion"] is None
