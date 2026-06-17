from sqlalchemy.ext.asyncio import AsyncSession

from mai.db.models import Repo
from mai.git.client import GitClient
from mai.repository.commits import CommitRepository
from mai.repository.cursors import CursorRepository

_CURSOR_SOURCE = "git_commit"


async def commits_harvest_repo(session: AsyncSession, client: GitClient, repo: Repo,
                               *, max_commits: int | None = None) -> int:
    """Ensure mirror, fetch, ingest new commits since the SHA cursor, advance the cursor.

    Commits are processed oldest-first; the cursor advances to the newest ingested SHA,
    so a `max_commits` batch resumes cleanly on the next run.
    """
    cursors = CursorRepository(session)
    commits = CommitRepository(session)

    await client.ensure_mirror(repo.core, repo.url)
    await client.fetch(repo.core)

    since = await cursors.get(repo.full_name, _CURSOR_SOURCE)
    metas = await client.new_commits(repo.core, since)
    if max_commits is not None:
        metas = metas[:max_commits]

    count = 0
    last_sha = since
    for meta in metas:
        if await commits.add_commit(repo.core, meta):
            count += 1
        last_sha = meta.sha

    if last_sha is not None and last_sha != since:
        await cursors.set(repo.full_name, _CURSOR_SOURCE, last_sha)
    return count
