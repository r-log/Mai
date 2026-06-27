import pytest_asyncio
from datetime import datetime, timezone
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mai.db.base import Base
from mai.db.models import PatchGroup, Commit, CommitFile, PortVerdict, ReviewAdvice
from mai.git.fake import FakeGitClient
from mai.judge.fake import FakeJudge
from mai.judge.schema import ReviewOpinion
from mai.sync.review import build_review_advice


@pytest_asyncio.fixture
async def cache_session():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(eng, expire_on_commit=False)() as s:
        yield s


async def test_review_advice_row_roundtrips(cache_session):
    cache_session.add(ReviewAdvice(
        patch_group_id="pg1", core="four", source_sha="s1", base_sha="b1",
        model="anthropic/claude-sonnet-4.6", prompt_version=1, assessment="divergent",
        confidence=0.6, reason="x", tips=["t"], citations=["c"], adapted_hunks=[], grounded=True))
    await cache_session.commit()
    row = await cache_session.scalar(select(ReviewAdvice).where(
        ReviewAdvice.patch_group_id == "pg1", ReviewAdvice.core == "four"))
    assert row.assessment == "divergent" and row.confidence == 0.6
    assert row.tips == ["t"] and row.grounded is True


PATCH = ("diff --git a/src/shared/Db.cpp b/src/shared/Db.cpp\n"
         "--- a/src/shared/Db.cpp\n+++ b/src/shared/Db.cpp\n"
         "@@ -1,3 +1,4 @@\n a\n b\n+x\n c\n")


@pytest_asyncio.fixture
async def seeded(cache_session):
    s = cache_session
    s.add(PatchGroup(id="pg1", patch_id="p1"))
    ts = datetime(2026, 6, 1, tzinfo=timezone.utc)
    cm = Commit(core="three", sha="sha123abcd", author="a", authored_at=ts,
                committer="a", committed_at=ts, message="db crash fix on shutdown")
    s.add(cm)
    await s.flush()
    s.add(CommitFile(commit_id=cm.id, path="src/shared/Db.cpp", subsystem="src/shared",
                     change_type="M", added_lines=2, removed_lines=1))
    s.add(PortVerdict(patch_group_id="pg1", core="four", verdict="review",
                      apply_result="conflict", relevance="portable", source_core="three",
                      source_sha="sha123abcd", subsystem="src/shared", magnitude=3,
                      tier="surgical", conflict_applied=1, conflict_total=1))
    await s.commit()
    return s


def _git():
    g = FakeGitClient()
    g._diffs = {("three", "sha123abcd"): PATCH}
    g._rejected = {("four", PATCH): {}}
    g._logs = {"four": []}
    g._heads = {"four": "base1"}
    return g


def _judge():
    return FakeJudge(opinion=ReviewOpinion(assessment="divergent", confidence=0.6,
                                           reason="differs"))


async def test_cache_miss_then_hit_skips_second_judge_call(seeded):
    git, judge = _git(), _judge()
    out1 = await build_review_advice(seeded, git, judge, "pg1:four")
    assert out1["opinion"]["assessment"] == "divergent"
    assert judge.calls == 1
    n = await seeded.scalar(select(func.count()).select_from(ReviewAdvice))
    assert n == 1                                    # row written
    out2 = await build_review_advice(seeded, git, judge, "pg1:four")
    assert out2["opinion"]["assessment"] == "divergent"
    assert judge.calls == 1                          # HIT: judge NOT called again


async def test_cache_invalidates_when_base_sha_moves(seeded):
    git, judge = _git(), _judge()
    await build_review_advice(seeded, git, judge, "pg1:four")
    assert judge.calls == 1
    git._heads["four"] = "base2"                     # target HEAD moved
    await build_review_advice(seeded, git, judge, "pg1:four")
    assert judge.calls == 2                           # MISS: recomputed


async def test_judge_failure_is_not_cached(seeded):
    git = _git()
    failing = FakeJudge(raises=RuntimeError("boom"))
    out = await build_review_advice(seeded, git, failing, "pg1:four")
    assert out["opinion"] is None
    n = await seeded.scalar(select(func.count()).select_from(ReviewAdvice))
    assert n == 0                                    # nothing cached
    ok = _judge()
    out2 = await build_review_advice(seeded, git, ok, "pg1:four")
    assert out2["opinion"]["assessment"] == "divergent" and ok.calls == 1
