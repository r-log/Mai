from mai.git.types import CommitMeta


class FakeGitClient:
    """In-memory GitClient for tests. `commits` maps core -> oldest-first CommitMeta list."""

    def __init__(self, commits: dict[str, list[CommitMeta]] | None = None, *,
                 diffs: dict[tuple[str, str], str] | None = None,
                 paths: dict[str, list[str]] | None = None,
                 apply_results: dict[tuple[str, str, bool], str] | None = None,
                 head_shas: dict[str, str] | None = None,
                 fractions: dict[tuple[str, str], tuple[int, int]] | None = None,
                 files: dict[tuple[str, str, str], str] | None = None,
                 rejected: dict[tuple[str, str], dict[str, str]] | None = None,
                 regions: dict[tuple[str, str], str] | None = None,
                 logs: dict[str, list[dict]] | None = None):
        self._commits = commits or {}
        self._diffs = diffs or {}
        self._paths = paths or {}
        self._apply = apply_results or {}
        self._heads = head_shas or {}
        self._fractions = fractions or {}
        self._files = files or {}
        self._rejected = rejected or {}
        self._regions = regions or {}
        self._logs = logs or {}

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

    async def read_file(self, core: str, ref: str, path: str) -> str | None:
        return self._files.get((core, ref, path))

    async def paths_exist(self, core: str, paths: list[str]) -> dict[str, bool]:
        have = set(self._paths.get(core, []))
        return {p: p in have for p in paths}

    async def ensure_worktree(self, core: str) -> str:
        return f"/fake/worktrees/{core}"

    async def apply_check(self, core: str, patch_text: str, *,
                          reverse: bool = False) -> str:
        key = (core, patch_text, reverse)
        if key in self._apply:
            return self._apply[key]
        return "conflict" if reverse else "clean"

    async def head_sha(self, core: str) -> str:
        return self._heads.get(core, f"head-{core}")

    async def apply_fraction(self, core: str, patch_text: str,
                             paths: list[str]) -> tuple[int, int]:
        return self._fractions.get((core, patch_text), (0, 1))

    async def rejected_hunks(self, core, patch_text, paths):
        return dict(self._rejected.get((core, patch_text), {}))

    async def read_region(self, core, path, start, end):
        return self._regions.get((core, path), "")

    async def log_touching(self, core, paths, *, limit=80):
        return list(self._logs.get(core, []))
