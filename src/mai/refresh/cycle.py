from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from mai.repository.repos import RepoRepository


@dataclass
class RefreshResult:
    new_commits: int
    harvested_repos: int
    port_candidates: int
    pages: int


async def run_refresh_cycle(
    session: AsyncSession,
    *,
    git_client,
    github_client=None,
    ledger_path: str,
    deploy_hook=None,
) -> RefreshResult:
    """Bring the engine + site up to date in one idempotent pass.

    Stages (each already cursor-gated/idempotent): commits-harvest ->
    PR-harvest -> sync-analyze -> publish, then an optional deploy.
    """
    from mai.git_harvest import commits_harvest_repo
    from mai.harvest import harvest_repo
    from mai.publish.site import publish_site
    from mai.sync.classify import classify_subsystems
    from mai.sync.portcandidates import compute_port_candidates
    from mai.sync.propagate import compute_propagation

    repos = await RepoRepository(session).all()

    new_commits = 0
    for repo in repos:
        new_commits += await commits_harvest_repo(session, git_client, repo)
        await session.commit()

    harvested = 0
    if github_client is not None:
        for repo in repos:
            await harvest_repo(session, github_client, repo)
            await session.commit()
            harvested += 1

    await compute_propagation(session)
    await classify_subsystems(session)
    pc = await compute_port_candidates(session)
    await session.commit()

    pages = await publish_site(session, ledger_path)

    if deploy_hook is not None:
        await deploy_hook.trigger()

    return RefreshResult(
        new_commits=new_commits,
        harvested_repos=harvested,
        port_candidates=pc["candidates"],
        pages=pages,
    )
