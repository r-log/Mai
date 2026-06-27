# tests/test_review_advice.py
from datetime import datetime, timezone
import pytest_asyncio
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mai.db.base import Base
from mai.db.models import PatchGroup, Commit, CommitFile, PortVerdict
from mai.git.fake import FakeGitClient
from mai.judge.fake import FakeJudge
from mai.judge.schema import ReviewOpinion
from mai.sync.review import build_review_advice

PATCH = ("diff --git a/src/shared/Db.cpp b/src/shared/Db.cpp\n"
         "--- a/src/shared/Db.cpp\n+++ b/src/shared/Db.cpp\n"
         "@@ -1,3 +1,4 @@\n a\n b\n+x\n c\n"
         "@@ -6,3 +7,3 @@\n ctx\n g\n-h\n+H\n")
REJ = "@@ -6,3 +7,3 @@\n ctx\n g\n-h\n+H\n"


@pytest_asyncio.fixture
async def session():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    f = async_sessionmaker(eng, expire_on_commit=False)
    async with f() as s:
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
                          tier="surgical", conflict_applied=1, conflict_total=2))
        await s.commit()
        yield s


def _git():
    git = FakeGitClient()
    git._diffs = {("three", "sha123abcd"): PATCH}
    git._rejected = {("four", PATCH): {"src/shared/Db.cpp": REJ}}
    git._regions = {("four", "src/shared/Db.cpp"): "g\nH\ni"}
    git._logs = {"four": []}
    return git


async def test_advice_returns_evidence_and_grounded_opinion(session):
    op = ReviewOpinion(assessment="portable", confidence=0.9, reason="clean",
                       tips=["adapt in src/shared/Db.cpp"], citations=["src/shared/Db.cpp"])
    judge = FakeJudge(opinion=op)
    out = await build_review_advice(session, _git(), judge, "pg1:four")
    assert out["evidence"]["core"] == "four"
    assert out["opinion"]["assessment"] == "portable"
    assert "adapt in src/shared/Db.cpp" in out["opinion"]["tips"]
    assert judge.calls == 1


async def test_advice_no_judge_yields_null_opinion(session):
    out = await build_review_advice(session, _git(), None, "pg1:four")
    assert out["evidence"] is not None
    assert out["opinion"] is None


async def test_invariant1_non_review_never_calls_judge(session):
    await session.execute(update(PortVerdict).values(verdict="needs"))
    await session.commit()
    judge = FakeJudge()
    out = await build_review_advice(session, _git(), judge, "pg1:four")
    assert out["evidence"] is None       # build_review_evidence returns None for non-review
    assert out["opinion"] is None
    assert judge.calls == 0              # Invariant 1: judge never invoked


async def test_judge_failure_degrades_to_null_opinion(session):
    judge = FakeJudge(raises=RuntimeError("boom"))
    out = await build_review_advice(session, _git(), judge, "pg1:four")
    assert out["evidence"] is not None
    assert out["opinion"] is None        # exception swallowed, evidence preserved
