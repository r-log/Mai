from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mai.db.models import CommitFile, DriftObservation
from mai.repository.subsystem_class import SubsystemClassRepository

# Conservative subsystem classifier. Rules seeded from workspace CLAUDE.md:
# pure-infrastructure prefixes are shared; path segments naming version-bound
# (Cata-vs-WotLK) content are expansion; everything else is mixed and is
# resolved at file granularity downstream (Phase 2c).
SHARED_PREFIXES = ("src/shared", "src/realmd", "src/tools", "src/framework")

# Vendored third-party libraries: cross-fork difference is structural (in-tree vs
# submodule), never actionable port-debt. Classified apart from shared/expansion/mixed.
VENDORED_PREFIXES = ("dep",)

EXPANSION_SEGMENTS = frozenset({
    "spell", "spells", "quest", "quests", "talent", "talents",
    "achievement", "achievements", "battleground", "battlegrounds",
    "arena", "arenas", "loot", "pet", "pets", "vehicle", "vehicles",
    "reputation", "scripts",
})

# Client/protocol-bound: byte layouts differ per WoW client build (15595 vs 12340 ...),
# so these are divergent-by-design and never cross-portable, even when text merges.
# NOTE: 'server' is intentionally absent — 'src/game/Server' stays 'mixed' via paths and
# is upgraded to client_bound only when the drift signal proves it fully diverged.
CLIENT_BOUND_SEGMENTS = frozenset({
    "worldhandlers", "opcode", "opcodes", "packet", "packets",
    "protocol", "smsg", "cmsg", "authsocket", "worldsocket",
})


def classify_subsystem(subsystem: str) -> str:
    """Return 'vendored' | 'client_bound' | 'shared' | 'expansion' | 'mixed' for a subsystem path (depth-3 dir).

    Conservative by design: 'vendored' for third-party deps, 'client_bound' for
    protocol/packet paths (divergent-by-design per client build), 'shared' only
    for infrastructure prefixes, 'expansion' only when a path segment names
    version-bound content, else 'mixed'.
    """
    s = subsystem.lower()
    for prefix in VENDORED_PREFIXES:
        if s == prefix or s.startswith(prefix + "/"):
            return "vendored"
    segments = s.split("/")
    if any(seg in CLIENT_BOUND_SEGMENTS for seg in segments):
        return "client_bound"
    for prefix in SHARED_PREFIXES:
        if s == prefix or s.startswith(prefix + "/"):
            return "shared"
    if any(seg in EXPANSION_SEGMENTS for seg in segments):
        return "expansion"
    return "mixed"


async def seed_client_bound_from_drift(session) -> int:
    """Upgrade fully-diverged 'mixed' subsystems to 'client_bound' (source 'drift').

    A subsystem is 'fully diverged' when every drift observation of it has
    identical == 0 and diverged > 0 (nothing matches across any fork pair) — the
    fingerprint of client/protocol-bound code (e.g. WorldHandlers/Server). Only
    upgrades subsystems the path heuristic left 'mixed'; never overrides a confident
    path class or a manual_override.
    """
    obs = list(await session.scalars(select(DriftObservation)))
    by_sub: dict[str, list[DriftObservation]] = defaultdict(list)
    for o in obs:
        by_sub[o.subsystem].append(o)

    repo = SubsystemClassRepository(session)
    seeded = 0
    for sub, rows in by_sub.items():
        if not all(r.identical == 0 and r.diverged > 0 for r in rows):
            continue
        current = await repo.get(sub)
        if current is None or current.classification != "mixed":
            continue  # only upgrade an unknown ('mixed') auto classification
        if await repo.upsert_auto(sub, "client_bound", source="drift"):
            seeded += 1
    await session.commit()
    return seeded


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
    counts = {"total": 0, "shared": 0, "expansion": 0, "mixed": 0,
              "vendored": 0, "client_bound": 0, "manual_preserved": 0}
    for subsystem in subsystems:
        auto = classify_subsystem(subsystem)
        wrote = await repo.upsert_auto(subsystem, auto)
        if wrote:
            counts[auto] += 1
        else:
            counts["manual_preserved"] += 1
            kept = await repo.get(subsystem)
            counts[kept.classification] = counts.get(kept.classification, 0) + 1
        counts["total"] += 1
    await session.commit()
    drift_seeded = await seed_client_bound_from_drift(session)
    counts["client_bound_from_drift"] = drift_seeded
    return counts
