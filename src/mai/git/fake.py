from mai.git.types import CommitMeta


class FakeGitClient:
    """In-memory GitClient for tests. `commits` maps core -> oldest-first CommitMeta list."""

    def __init__(self, commits: dict[str, list[CommitMeta]] | None = None, *,
                 diffs: dict[tuple[str, str], str] | None = None,
                 paths: dict[str, list[str]] | None = None,
                 apply_results: dict[tuple[str, str, bool], str] | None = None):
        self._commits = commits or {}
        self._diffs = diffs or {}
        self._paths = paths or {}
        self._apply = apply_results or {}

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

    async def diff(self, core: str, sha: str) -> str:
        return self._diffs.get((core, sha), "")

    async def paths_exist(self, core: str, paths: list[str]) -> dict[str, bool]:
        have = set(self._paths.get(core, []))
        return {p: p in have for p in paths}
