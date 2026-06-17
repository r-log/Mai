import re

# Matches git's standard backport trailer: "(cherry picked from commit <hex>)".
_CHERRY = re.compile(r"cherry picked from commit ([0-9a-f]{7,40})", re.IGNORECASE)


def parse_cherry_sources(message: str) -> list[str]:
    """Return the source SHAs cited by cherry-pick trailers, deduped, in first-seen order."""
    if not message:
        return []
    return list(dict.fromkeys(_CHERRY.findall(message)))
