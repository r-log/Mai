import re
from dataclasses import dataclass

_REPO_RE = re.compile(r"https://github\.com/([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+)")
_CORE_BY_ORG = {
    "mangoszero": "zero", "mangosone": "one", "mangostwo": "two",
    "mangosthree": "three", "mangosfour": "four",
}


@dataclass(frozen=True)
class RegistryRow:
    full_name: str
    core: str
    url: str


def parse_registry(readme_markdown: str) -> list[RegistryRow]:
    seen: dict[str, RegistryRow] = {}
    for org, repo in _REPO_RE.findall(readme_markdown):
        full_name = f"{org}/{repo}"
        if full_name in seen:
            continue
        seen[full_name] = RegistryRow(
            full_name=full_name,
            core=_CORE_BY_ORG.get(org.lower(), "other"),
            url=f"https://github.com/{full_name}",
        )
    return sorted(seen.values(), key=lambda r: r.full_name)
