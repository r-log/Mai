from sqlalchemy import func, select

from mai.db.models import Commit, Repo
from mai.git.fake import FakeGitClient
from mai.git.types import CommitFileMeta, CommitMeta
from mai.git_harvest import commits_harvest_repo
from mai.repository.cursors import CursorRepository

REPO = Repo(full_name="r-log/server", core="three",
            url="file:///dev/null")


def _c(sha: str, *, is_merge: bool = False) -> CommitMeta:
    files = [] if is_merge else [CommitFileMeta(path="src/a.cpp", change_type="M",
                                                added=1, removed=0)]
    return CommitMeta(sha=sha, author="d", authored_at="2026-01-01T00:00:00Z",
                      committer="d", committed_at="2026-01-01T00:00:00Z",
                      message=sha, parents=["x", "y"] if is_merge else ["x"],
                      is_merge=is_merge, patch_id=None if is_merge else f"p-{sha}",
                      files=files)


async def test_harvest_ingests_and_advances_cursor(session):
    session.add(REPO)
    await session.flush()
    client = FakeGitClient({"three": [_c("s1"), _c("s2")]})
    n = await commits_harvest_repo(session, client, REPO)
    await session.commit()
    assert n == 2
    assert await session.scalar(select(func.count()).select_from(Commit)) == 2
    assert await CursorRepository(session).get("r-log/server", "git_commit") == "s2"


async def test_harvest_idempotent_second_run(session):
    session.add(REPO)
    await session.flush()
    client = FakeGitClient({"three": [_c("s1"), _c("s2")]})
    await commits_harvest_repo(session, client, REPO)
    await session.commit()
    n2 = await commits_harvest_repo(session, client, REPO)
    await session.commit()
    assert n2 == 0
    assert await session.scalar(select(func.count()).select_from(Commit)) == 2


async def test_harvest_incremental_after_new_commit(session):
    session.add(REPO)
    await session.flush()
    commits = [_c("s1"), _c("s2")]
    client = FakeGitClient({"three": commits})
    await commits_harvest_repo(session, client, REPO)
    await session.commit()
    commits.append(_c("s3"))
    n = await commits_harvest_repo(session, client, REPO)
    await session.commit()
    assert n == 1
    assert await CursorRepository(session).get("r-log/server", "git_commit") == "s3"


async def test_harvest_max_commits_batches(session):
    session.add(REPO)
    await session.flush()
    client = FakeGitClient({"three": [_c("s1"), _c("s2"), _c("s3")]})
    n1 = await commits_harvest_repo(session, client, REPO, max_commits=2)
    await session.commit()
    assert n1 == 2
    assert await CursorRepository(session).get("r-log/server", "git_commit") == "s2"
    n2 = await commits_harvest_repo(session, client, REPO, max_commits=2)
    await session.commit()
    assert n2 == 1
    assert await CursorRepository(session).get("r-log/server", "git_commit") == "s3"
