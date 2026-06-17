from typing import Protocol

from mai.git.types import CommitMeta


class GitError(RuntimeError):
    """A git subprocess returned non-zero."""


class GitClient(Protocol):
    async def ensure_mirror(self, core: str, url: str) -> None: ...
    async def fetch(self, core: str) -> None: ...
    async def new_commits(self, core: str, since_sha: str | None) -> list[CommitMeta]: ...
