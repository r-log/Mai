from itertools import combinations

from sqlalchemy.ext.asyncio import AsyncSession

from mai.drift.client import TreeClient
from mai.drift.compare import compare_trees
from mai.repository.drift import DriftRepository
from mai.repository.repos import RepoRepository


async def compute_drift(session: AsyncSession, client: TreeClient,
                        pairs: list[tuple[str, str]], depth: int = 3) -> int:
    """For each fork pair, fetch trees, compare, and store per-subsystem drift."""
    drepo = DriftRepository(session)
    rows = 0
    for fork_a, fork_b in pairs:
        tree_a = await client.get_tree(fork_a)
        tree_b = await client.get_tree(fork_b)
        for subsystem, stats in compare_trees(tree_a, tree_b, depth).items():
            await drepo.upsert(fork_a, fork_b, subsystem, stats)
            rows += 1
        await session.commit()  # one commit per pair; re-run is safe (idempotent upsert)
    return rows


async def default_pairs(session: AsyncSession) -> list[tuple[str, str]]:
    """All unordered pairs of tracked `*/server` repos."""
    repos = await RepoRepository(session).all()
    servers = sorted(r.full_name for r in repos if r.full_name.endswith("/server"))
    return list(combinations(servers, 2))
