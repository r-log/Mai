from mai.git.types import CommitMeta


class FakeGitClient:
    """In-memory GitClient for tests. `commits` maps core -> oldest-first CommitMeta list."""

    def __init__(self, commits: dict[str, list[CommitMeta]] | None = None):
        self._commits = commits or {}

    async def ensure_mirror(self, core: str, url: str) -> None:
        return None

    async def fetch(self, core: str) -> None:
        return None

    async def new_commits(self, core: str, since_sha: str | None) -> list[CommitMeta]:
        items = self._commits.get(core, [])
        if since_sha is None:
            return list(items)
        shas = [c.sha for c in items]
        if since_sha in shas:
            return list(items[shas.index(since_sha) + 1:])
        return list(items)  # unknown cursor (e.g. force-push) -> re-walk all
