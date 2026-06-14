class FakeGitHubClient:
    """In-memory GitHubClient for tests. Honors `since` by filtering on updated_at."""

    def __init__(self, issues: dict[str, list[dict]] | None = None,
                 pulls: dict[str, list[dict]] | None = None):
        self._issues = issues or {}
        self._pulls = pulls or {}

    async def list_issues(self, repo: str, since: str | None = None) -> list[dict]:
        return self._filter(self._issues.get(repo, []), since)

    async def list_pulls(self, repo: str, since: str | None = None) -> list[dict]:
        return self._filter(self._pulls.get(repo, []), since)

    @staticmethod
    def _filter(items: list[dict], since: str | None) -> list[dict]:
        if since is None:
            return list(items)
        return [i for i in items if i["updated_at"] > since]
