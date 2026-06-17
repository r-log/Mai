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
