from typing import Protocol

import httpx


class TreeClient(Protocol):
    async def get_tree(self, repo: str) -> dict[str, str]: ...


class GitHubTreeClient:
    """Production TreeClient backed by the GitHub Trees API (recursive, blob SHAs)."""

    def __init__(self, token: str, ref: str = "HEAD",
                 base_url: str = "https://api.github.com",
                 client: httpx.AsyncClient | None = None):
        self._ref = ref
        self._base = base_url.rstrip("/")
        # caller should inject a managed client (async with ...) in production
        self._client = client or httpx.AsyncClient()
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def get_tree(self, repo: str) -> dict[str, str]:
        resp = await self._client.get(
            f"{self._base}/repos/{repo}/git/trees/{self._ref}",
            params={"recursive": "1"},
            headers=self._headers,
        )
        resp.raise_for_status()
        data = resp.json()
        return {item["path"]: item["sha"]
                for item in data.get("tree", [])
                if item.get("type") == "blob"}
