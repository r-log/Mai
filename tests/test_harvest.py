from sqlalchemy import func, select

from mai.db.models import Report, SourceRecord, Repo
from mai.github.fake import FakeGitHubClient
from mai.harvest import harvest_repo
from mai.repository.cursors import CursorRepository

REPO_NAME = "mangoszero/server"
ISSUES = [
    {"number": 1, "title": "Bug A", "state": "open", "updated_at": "2026-01-01T00:00:00Z"},
    {"number": 2, "title": "PR-in-issues", "state": "closed",
     "updated_at": "2026-01-02T00:00:00Z", "pull_request": {"url": "x"}},
]
PULLS = [
    {"number": 10, "title": "Fix A", "state": "closed",
     "merged_at": "2026-01-03T00:00:00Z", "updated_at": "2026-01-03T00:00:00Z"},
]


def _repo() -> Repo:
    return Repo(full_name=REPO_NAME, core="zero", url=f"https://github.com/{REPO_NAME}")


async def test_harvest_ingests_issues_and_pulls_skipping_pr_in_issues(session):
    session.add(_repo())
    await session.flush()
    client = FakeGitHubClient(issues={REPO_NAME: list(ISSUES)}, pulls={REPO_NAME: list(PULLS)})
    await harvest_repo(session, client, _repo())
    await session.commit()
    assert await session.scalar(select(func.count()).select_from(SourceRecord)) == 2
    assert await session.scalar(select(func.count()).select_from(Report)) == 2
    keys = set(await session.scalars(select(Report.canonical_key)))
    assert keys == {"gh_issue:mangoszero/server#1", "gh_pr:mangoszero/server#10"}


async def test_harvest_advances_cursor_to_newest_seen(session):
    session.add(_repo())
    await session.flush()
    client = FakeGitHubClient(issues={REPO_NAME: list(ISSUES)}, pulls={REPO_NAME: list(PULLS)})
    await harvest_repo(session, client, _repo())
    await session.commit()
    cur = CursorRepository(session)
    # newest issue-stream item seen is the skipped PR-in-issues (#2, 2026-01-02)
    assert await cur.get(REPO_NAME, "gh_issue") == "2026-01-02T00:00:00Z"
    assert await cur.get(REPO_NAME, "gh_pr") == "2026-01-03T00:00:00Z"


async def test_harvest_is_incremental_on_second_run(session):
    session.add(_repo())
    await session.flush()
    issues = list(ISSUES)
    client = FakeGitHubClient(issues={REPO_NAME: issues}, pulls={REPO_NAME: list(PULLS)})
    await harvest_repo(session, client, _repo())
    await session.commit()
    before = await session.scalar(select(func.count()).select_from(SourceRecord))
    # FakeGitHubClient holds a reference to this list, so the append feeds run #2
    issues.append({"number": 3, "title": "Bug C", "state": "open",
                   "updated_at": "2026-05-01T00:00:00Z"})
    await harvest_repo(session, client, _repo())
    await session.commit()
    after = await session.scalar(select(func.count()).select_from(SourceRecord))
    assert after == before + 1  # only the newer issue #3 was fetched + ingested
