# tests/test_review_evidence.py
from datetime import datetime, timezone
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from mai.db.base import Base
from mai.db.models import PatchGroup, Commit, CommitFile, PortVerdict
from mai.git.fake import FakeGitClient
from mai.sync.review import build_review_evidence, _rank_similar, _classify_type

PATCH = ("diff --git a/src/shared/Db.cpp b/src/shared/Db.cpp\n"
         "--- a/src/shared/Db.cpp\n+++ b/src/shared/Db.cpp\n"
         "@@ -1,3 +1,4 @@\n a\n b\n+x\n c\n"
         "@@ -6,3 +7,3 @@\n ctx\n g\n-h\n+H\n")
REJ = "@@ -6,3 +7,3 @@\n ctx\n g\n-h\n+H\n"

@pytest_asyncio.fixture
async def session():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as c: await c.run_sync(Base.metadata.create_all)
    f = async_sessionmaker(eng, expire_on_commit=False)
    async with f() as s:
        s.add(PatchGroup(id="pg1", patch_id="p1"))
        ts = datetime(2026, 6, 1, tzinfo=timezone.utc)
        cm = Commit(core="three", sha="sha123abcd", author="a", authored_at=ts,
                    committer="a", committed_at=ts,
                    message="db crash fix on shutdown\n\nFrees the cache first.")
        s.add(cm); await s.flush()
        s.add(CommitFile(commit_id=cm.id, path="src/shared/Db.cpp", subsystem="src/shared",
                         change_type="M", added_lines=2, removed_lines=1))
        s.add(PortVerdict(patch_group_id="pg1", core="four", verdict="review",
                          apply_result="conflict", relevance="portable", source_core="three",
                          source_sha="sha123abcd", subsystem="src/shared", magnitude=3,
                          tier="surgical", conflict_applied=1, conflict_total=2))
        await s.commit(); yield s

async def test_evidence_marks_rejected_hunk_with_target_context(session):
    git = FakeGitClient()
    git._diffs = {("three", "sha123abcd"): PATCH}
    git._rejected = {("four", PATCH): {"src/shared/Db.cpp": REJ}}
    git._regions = {("four", "src/shared/Db.cpp"): "g\nH-renamed\ni"}
    git._logs = {"four": [{"sha": "deadbeef00", "date": "2026-03-01",
                           "title": "rework db teardown"}]}
    ev = await build_review_evidence(session, git, "pg1:four")
    assert ev["core"] == "four"
    assert ev["fix"]["title"] == "db crash fix on shutdown"
    assert ev["fix"]["type"] == "bugfix"
    hunks = ev["conflict"]["hunks"]
    assert len(hunks) == 2
    rejected = [h for h in hunks if not h["applied"]]
    assert len(rejected) == 1 and rejected[0]["target_context"]   # context attached
    assert ev["conflict"]["applied"] == 1 and ev["conflict"]["total"] == 2
    assert ev["similar"][0]["title"] == "rework db teardown"

async def test_non_review_returns_none(session):
    # flip the verdict to needs -> no evidence
    from sqlalchemy import update
    from mai.db.models import PortVerdict as PV
    await session.execute(update(PV).values(verdict="needs"))
    await session.commit()
    assert await build_review_evidence(session, FakeGitClient(), "pg1:four") is None

def test_rank_similar_orders_by_title_overlap():
    rows = [{"sha":"a","date":"d","title":"unrelated cleanup"},
            {"sha":"b","date":"d","title":"db crash on shutdown"}]
    out = _rank_similar(rows, "db crash fix on shutdown", limit=2)
    assert out[0]["sha"] == "b" and out[0]["score"] > 0
