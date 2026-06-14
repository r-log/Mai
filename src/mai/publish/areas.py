# Canonical area palette (GitHub-label style: light bg + readable text).
AREAS = [
    {"name": "Movement",  "slug": "movement",  "color": "#0969da"},
    {"name": "Spell",     "slug": "spell",     "color": "#8250df"},
    {"name": "Combat",    "slug": "combat",    "color": "#cf222e"},
    {"name": "Quest",     "slug": "quest",     "color": "#1a7f37"},
    {"name": "Loot",      "slug": "loot",      "color": "#9a6700"},
    {"name": "Item",      "slug": "item",      "color": "#bc4c00"},
    {"name": "Creature",  "slug": "creature",  "color": "#bf3989"},
    {"name": "Character", "slug": "character", "color": "#6639ba"},
    {"name": "World",     "slug": "world",     "color": "#0c7489"},
    {"name": "Database",  "slug": "database",  "color": "#57606a"},
    {"name": "Tools",     "slug": "tools",     "color": "#424a53"},
    {"name": "Network",   "slug": "network",   "color": "#4f46c4"},
    {"name": "Other",     "slug": "other",     "color": "#59636e"},
]

# First keyword match wins; order matters.
_KEYWORDS = [
    ("Movement",  ["movement", "teleport", "speed", "fly", "mount", "fall", "jump",
                   "navi", "mmap", "pathfind", "waypoint"]),
    ("Spell",     ["spell", "aura", "cast", "mana", "cooldown", "rune", "proc", "buff"]),
    ("Combat",    ["combat", "damage", "melee", "threat", "aggro", "agro", "crit",
                   "resil", "pvp", "block", "parry"]),
    ("Loot",      ["loot", "lootable", "drop ", "corpse", "skinning"]),
    ("Quest",     ["quest", "objective", "gossip", "escort"]),
    ("Item",      ["item", "equip", "enchant", "inventory", "bag", "gem"]),
    ("Creature",  ["creature", "npc", "mob", "pet", "beast", "tame", "vendor",
                   "trainer", "guard", "devilsaur"]),
    ("Character", ["character", "level", "race", "class", "talent", "experience",
                   "starting", "stat"]),
    ("World",     ["zone", "area", "map", "vmap", "instance", "raid", "dungeon",
                   "gameobject", "transport", "tram", "elevator"]),
    ("Database",  ["database", "sql", "db_version", "table"]),
    ("Tools",     ["extractor", "cmake", "compile", "build", "dbc editor", "tool"]),
    ("Network",   ["packet", "opcode", "socket", "realmd", "login", "disconnect"]),
]

_ENTITY_AREA = [("spell", "Spell"), ("npc", "Creature"), ("quest", "Quest"),
                ("item", "Item"), ("zone", "World")]


def _match_keywords(text: str) -> str | None:
    text = (text or "").lower()
    for area, kws in _KEYWORDS:
        if any(kw in text for kw in kws):
            return area
    return None


def area_of(title: str, enrichment: dict | None, source_payload: dict) -> str:
    """Classify a bug into a canonical area. Precedence: IPS category, then
    enrichment entities, then title keywords, then Other."""
    cat = " ".join(str(source_payload.get(k, "") or "")
                   for k in ("sub_category", "main_category"))
    hit = _match_keywords(cat)
    if hit:
        return hit
    if enrichment:
        entities = enrichment.get("affected_entities") or {}
        for key, area in _ENTITY_AREA:
            if entities.get(key):
                return area
    hit = _match_keywords(title)
    if hit:
        return hit
    return "Other"
