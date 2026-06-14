from typing import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from mai.contracts import IntakeEvent
from mai.db.models import Repo
from mai.github.client import GitHubClient
from mai.github.constants import SOURCE_GH_ISSUE, SOURCE_GH_PR
from mai.github.normalize import normalize_issue, normalize_pull
from mai.ingest import ingest_event
from mai.repository.cursors import CursorRepository


async def _ingest_stream(session: AsyncSession, items: list[dict], repo: Repo,
                         normalize_fn: Callable[[str, str, dict], IntakeEvent | None]) -> str | None:
    """Ingest each normalizable item; return the newest updated_at SEEN (incl. skipped)."""
    newest = None
    for item in items:
        ts = item.get("updated_at")
        if ts is not None and (newest is None or ts > newest):
            newest = ts
        evt = normalize_fn(repo.full_name, repo.core, item)
        if evt is not None:
            await ingest_event(session, evt)
    return newest


async def harvest_repo(session: AsyncSession, client: GitHubClient, repo: Repo) -> None:
    cursors = CursorRepository(session)

    since_i = await cursors.get(repo.full_name, SOURCE_GH_ISSUE)
    issues = await client.list_issues(repo.full_name, since_i)
    newest_i = await _ingest_stream(session, issues, repo, normalize_issue)
    if newest_i is not None:
        await cursors.set(repo.full_name, SOURCE_GH_ISSUE, newest_i)

    since_p = await cursors.get(repo.full_name, SOURCE_GH_PR)
    pulls = await client.list_pulls(repo.full_name, since_p)
    newest_p = await _ingest_stream(session, pulls, repo, normalize_pull)
    if newest_p is not None:
        await cursors.set(repo.full_name, SOURCE_GH_PR, newest_p)
