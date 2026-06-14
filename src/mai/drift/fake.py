class FakeTreeClient:
    """In-memory TreeClient for tests: repo full_name -> {path: blob_sha}."""

    def __init__(self, trees: dict[str, dict[str, str]]):
        self._trees = trees

    async def get_tree(self, repo: str) -> dict[str, str]:
        return dict(self._trees.get(repo, {}))
