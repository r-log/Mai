import httpx
from typing import Protocol

_PER_PAGE = 100


class GitHubClient(Protocol):
    async def list_issues(self, repo: str, since: str | None = None) -> list[dict]: ...
    async def list_pulls(self, repo: str, since: str | None = None) -> list[dict]: ...


class HttpGitHubClient:
    """Production GitHubClient backed by httpx. Pass `client` to inject a transport."""

    def __init__(self, token: str, base_url: str = "https://api.github.com",
                 client: httpx.AsyncClient | None = None):
        self._base = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient()  # caller should inject a managed client (async with ...) in production
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def list_issues(self, repo: str, since: str | None = None) -> list[dict]:
        params = {"state": "all", "sort": "updated", "direction": "asc"}
        if since is not None:
            params["since"] = since
        return await self._paginate(f"/repos/{repo}/issues", params)

    async def list_pulls(self, repo: str, since: str | None = None) -> list[dict]:
        # The GitHub pulls API has no `since`; fetch newest-first and stop early
        # once we cross the cursor, instead of downloading every PR each run.
        path = f"/repos/{repo}/pulls"
        params = {"state": "all", "sort": "updated", "direction": "desc"}
        results: list[dict] = []
        page = 1
        while True:
            resp = await self._client.get(
                self._base + path,
                params={**params, "per_page": _PER_PAGE, "page": page},
                headers=self._headers,
            )
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            for item in batch:
                if since is not None and item["updated_at"] <= since:
                    return results
                results.append(item)
            if len(batch) < _PER_PAGE:
                break
            page += 1
        return results

    async def _paginate(self, path: str, params: dict) -> list[dict]:
        results: list[dict] = []
        page = 1
        while True:
            resp = await self._client.get(
                self._base + path,
                params={**params, "per_page": _PER_PAGE, "page": page},
                headers=self._headers,
            )
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            results.extend(batch)
            if len(batch) < _PER_PAGE:
                break
            page += 1
        return results
