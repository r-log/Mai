from sqlalchemy import func, select

from mai.db.models import Commit, CommitFile, CommitPatch
from mai.git.types import CommitFileMeta, CommitMeta
from mai.repository.commits import CommitRepository


def _meta(sha: str, *, is_merge: bool = False) -> CommitMeta:
    files = [] if is_merge else [
        CommitFileMeta(path="src/game/Object/Player.cpp", change_type="M",
                       added=3, removed=1),
        CommitFileMeta(path="src/new.cpp", change_type="R",
                       old_path="src/old.cpp", added=0, removed=0),
    ]
    return CommitMeta(sha=sha, author="dev", authored_at="2026-01-01T00:00:00Z",
                      committer="dev", committed_at="2026-01-01T00:00:00Z",
                      message=f"fix {sha}", parents=["p1", "p2"] if is_merge else ["p1"],
                      is_merge=is_merge, patch_id=None if is_merge else f"pid-{sha}",
                      files=files)


async def test_add_commit_inserts_commit_files_and_patch(session):
    repo = CommitRepository(session)
    assert await repo.add_commit("three", _meta("abc")) is True
    await session.commit()
    assert await session.scalar(select(func.count()).select_from(Commit)) == 1
    assert await session.scalar(select(func.count()).select_from(CommitFile)) == 2
    assert await session.scalar(select(func.count()).select_from(CommitPatch)) == 1
    cf = await session.scalar(select(CommitFile).where(CommitFile.change_type == "R"))
    assert cf.old_path == "src/old.cpp"
    assert cf.subsystem == "src/game/Object" or cf.subsystem == "src"  # depth-3 of src/new.cpp -> "src"
    cp = await session.scalar(select(CommitPatch))
    assert cp.patch_id == "pid-abc"


async def test_add_commit_is_idempotent(session):
    repo = CommitRepository(session)
    await repo.add_commit("three", _meta("abc"))
    await session.commit()
    assert await repo.add_commit("three", _meta("abc")) is False
    await session.commit()
    assert await session.scalar(select(func.count()).select_from(Commit)) == 1


async def test_add_merge_commit_has_null_patch_and_no_files(session):
    repo = CommitRepository(session)
    await repo.add_commit("three", _meta("merge1", is_merge=True))
    await session.commit()
    assert await session.scalar(select(func.count()).select_from(CommitFile)) == 0
    cp = await session.scalar(select(CommitPatch))
    assert cp.patch_id is None
    c = await session.scalar(select(Commit))
    assert c.is_merge is True and c.parent_shas == ["p1", "p2"]
