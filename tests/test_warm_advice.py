# tests/test_warm_advice.py
from datetime import datetime, timezone
import pytest_asyncio
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mai.db.base import Base
from mai.db.models import PatchGroup, Commit, CommitFile, PortVerdict, ReviewAdvice
from mai.git.fake import FakeGitClient
from mai.judge.fake import FakeJudge
from mai.judge.schema import ReviewOpinion
from mai.orchestrate.warm import _warm_plan, warm_advice

PATCH = ("diff --git a/src/shared/Db.cpp b/src/shared/Db.cpp\n"
         "--- a/src/shared/Db.cpp\n+++ b/src/shared/Db.cpp\n"
         "@@ -1,3 +1,4 @@\n a\n b\n+x\n c\n")


def _engine_factory(tmp_path):
    # temp-FILE sqlite so per-worker sessions share data (in-memory would not).
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/warm.db")
    return eng, async_sessionmaker(eng, expire_on_commit=False)


async def _seed_review(s, pg_id, core, sha, applied, total, *, with_advice=False):
    s.add(PatchGroup(id=pg_id, patch_id="p-" + pg_id))
    ts = datetime(2026, 6, 1, tzinfo=timezone.utc)
    cm = Commit(core="three", sha=sha, author="a", authored_at=ts, committer="a",
                committed_at=ts, message="db crash fix on shutdown")
    s.add(cm)
    await s.flush()
    s.add(CommitFile(commit_id=cm.id, path="src/shared/Db.cpp", subsystem="src/shared",
                     change_type="M", added_lines=2, removed_lines=1))
    s.add(PortVerdict(patch_group_id=pg_id, core=core, verdict="review",
                      apply_result="conflict", relevance="portable", source_core="three",
                      source_sha=sha, subsystem="src/shared", magnitude=3, tier="surgical",
                      conflict_applied=applied, conflict_total=total))
    if with_advice:
        s.add(ReviewAdvice(patch_group_id=pg_id, core=core, source_sha=sha, base_sha="b",
                           model="m", prompt_version=1, assessment="divergent",
                           confidence=0.5, reason="x"))


@pytest_asyncio.fixture
async def factory(tmp_path):
    eng, fac = _engine_factory(tmp_path)
    async with eng.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    async with fac() as s:
        await _seed_review(s, "pg_near", "four", "shaNEAR0001", 5, 5)        # near (1.0)
        await _seed_review(s, "pg_part", "four", "shaPART0001", 2, 4)        # partial (0.5)
        await _seed_review(s, "pg_far", "four", "shaFAR00001", 1, 5)         # far (0.2) -> excluded
        await _seed_review(s, "pg_cached", "four", "shaCACHED01", 5, 5, with_advice=True)  # skip
        await s.commit()
    return fac


def _git():
    g = FakeGitClient(files={})
    g._diffs = {("three", "shaNEAR0001"): PATCH, ("three", "shaPART0001"): PATCH}
    g._rejected = {("four", PATCH): {}}
    g._logs = {"four": []}
    g._heads = {"four": "baseX"}
    return g


def _judge():
    return FakeJudge(opinion=ReviewOpinion(assessment="divergent", confidence=0.5, reason="d"))


async def test_warm_plan_filters_prioritizes_and_skips(factory):
    async with factory() as s:
        plan = await _warm_plan(s, limit=10)
    # only near + partial, near first; far excluded; cached excluded
    assert plan == ["pg_near:four", "pg_part:four"]


async def test_warm_plan_respects_limit(factory):
    async with factory() as s:
        plan = await _warm_plan(s, limit=1)
    assert plan == ["pg_near:four"]            # near prioritized


async def test_warm_advice_warms_then_idempotent(factory):
    git, judge = _git(), _judge()
    r1 = await warm_advice(factory, git, judge, limit=10, concurrency=2)
    assert r1["planned"] == 2 and r1["warmed"] == 2 and r1["failed"] == 0
    async with factory() as s:
        n = await s.scalar(select(func.count()).select_from(ReviewAdvice))
    assert n == 3                              # 2 newly warmed + the 1 pre-seeded
    r2 = await warm_advice(factory, git, judge, limit=10, concurrency=2)
    assert r2["planned"] == 0 and r2["warmed"] == 0    # all cached now


async def test_warm_advice_failure_isolated(factory):
    git = _git()
    failing = FakeJudge(raises=RuntimeError("boom"))
    r = await warm_advice(factory, git, failing, limit=10, concurrency=2)
    assert r["planned"] == 2 and r["warmed"] == 0 and r["failed"] == 2   # both fail, no raise
