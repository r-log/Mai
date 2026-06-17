from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mai.db.models import CommitFile
from mai.repository.subsystem_class import SubsystemClassRepository

# Conservative subsystem classifier. Rules seeded from workspace CLAUDE.md:
# pure-infrastructure prefixes are shared; path segments naming version-bound
# (Cata-vs-WotLK) content are expansion; everything else is mixed and is
# resolved at file granularity downstream (Phase 2c).
SHARED_PREFIXES = ("src/shared", "dep", "src/realmd", "src/tools", "src/framework")

EXPANSION_SEGMENTS = frozenset({
    "spell", "spells", "quest", "quests", "talent", "talents",
    "achievement", "achievements", "battleground", "battlegrounds",
    "arena", "arenas", "loot", "pet", "pets", "vehicle", "vehicles",
    "reputation", "scripts",
})


def classify_subsystem(subsystem: str) -> str:
    """Return 'shared' | 'expansion' | 'mixed' for a subsystem path (depth-3 dir).

    Conservative by design: 'shared' only for infrastructure prefixes, 'expansion'
    only when a path segment names version-bound content, else 'mixed'.
    """
    s = subsystem.lower()
    for prefix in SHARED_PREFIXES:
        if s == prefix or s.startswith(prefix + "/"):
            return "shared"
    if any(seg in EXPANSION_SEGMENTS for seg in s.split("/")):
        return "expansion"
    return "mixed"


async def classify_subsystems(session: AsyncSession) -> dict:
    """Classify every distinct harvested subsystem, preserving manual overrides.

    Reads `distinct CommitFile.subsystem` (offline), applies `classify_subsystem`,
    and upserts SubsystemClass rows. A row authored as `manual_override` is kept
    and counted under its existing classification. Recomputable.
    """
    subsystems = sorted(
        await session.scalars(select(CommitFile.subsystem).distinct())
    )
    repo = SubsystemClassRepository(session)
    counts = {"total": 0, "shared": 0, "expansion": 0, "mixed": 0, "manual_preserved": 0}
    for subsystem in subsystems:
        auto = classify_subsystem(subsystem)
        wrote = await repo.upsert_auto(subsystem, auto)
        if wrote:
            counts[auto] += 1
        else:
            counts["manual_preserved"] += 1
            kept = await repo.get(subsystem)
            counts[kept.classification] += 1
        counts["total"] += 1
    await session.commit()
    return counts
