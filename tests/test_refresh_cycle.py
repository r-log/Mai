from sqlalchemy import func, select

from mai.db.models import Commit, Report, Repo
from mai.git.fake import FakeGitClient
from mai.git.types import CommitFileMeta, CommitMeta
from mai.github.fake import FakeGitHubClient
from mai.refresh.cycle import run_refresh_cycle
from mai.refresh.fake import FakeDeployHook


def _repo() -> Repo:
    return Repo(full_name="r-log/server", core="three", url="file:///dev/null")


PULLS = [{"number": 10, "title": "Fix A", "state": "closed",
          "merged_at": "2026-01-03T00:00:00Z",
          "updated_at": "2026-01-03T00:00:00Z"}]


def _c(sha: str) -> CommitMeta:
    return CommitMeta(
        sha=sha, author="d", authored_at="2026-01-01T00:00:00Z",
        committer="d", committed_at="2026-01-01T00:00:00Z", message=sha,
        parents=["x"], is_merge=False, patch_id=f"p-{sha}",
        files=[CommitFileMeta(path="src/a.cpp", change_type="M",
                              added=1, removed=0)])


async def test_cycle_harvests_commits_and_prs_and_publishes(session, tmp_path):
    session.add(_repo())
    await session.commit()
    git = FakeGitClient({"three": [_c("s1"), _c("s2")]})
    gh = FakeGitHubClient(issues={"r-log/server": []},
                          pulls={"r-log/server": list(PULLS)})
    deploy = FakeDeployHook()

    result = await run_refresh_cycle(
        session, git_client=git, github_client=gh,
        ledger_path=str(tmp_path), deploy_hook=deploy)

    assert result.new_commits == 2
    assert result.harvested_repos == 1
    assert deploy.calls == 1
    assert result.pages >= 1
    assert await session.scalar(select(func.count()).select_from(Commit)) == 2
    assert await session.scalar(select(func.count()).select_from(Report)) == 1


async def test_cycle_is_idempotent(session, tmp_path):
    session.add(_repo())
    await session.commit()
    git = FakeGitClient({"three": [_c("s1"), _c("s2")]})
    gh = FakeGitHubClient(issues={"r-log/server": []},
                          pulls={"r-log/server": list(PULLS)})
    await run_refresh_cycle(session, git_client=git, github_client=gh,
                            ledger_path=str(tmp_path))
    again = await run_refresh_cycle(session, git_client=git, github_client=gh,
                                    ledger_path=str(tmp_path))
    assert again.new_commits == 0
    assert await session.scalar(select(func.count()).select_from(Commit)) == 2


async def test_cycle_without_github_skips_pr_harvest(session, tmp_path):
    session.add(_repo())
    await session.commit()
    git = FakeGitClient({"three": [_c("s1")]})

    result = await run_refresh_cycle(
        session, git_client=git, github_client=None, ledger_path=str(tmp_path))

    assert result.harvested_repos == 0
    assert await session.scalar(select(func.count()).select_from(Report)) == 0
