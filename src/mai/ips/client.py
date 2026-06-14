import re
from typing import Protocol

import httpx

_BUG_URL_RE = re.compile(r"/bug-tracker/.+-r\d+/?$")


class IpsClient(Protocol):
    async def list_bug_urls(self) -> list[str]: ...
    async def fetch_bug(self, url: str) -> str: ...


class FirecrawlIpsClient:
    """Production IpsClient backed by the Firecrawl API (map + scrape)."""

    def __init__(self, api_key: str,
                 base_url: str = "https://api.firecrawl.dev",
                 bug_tracker_url: str = "https://www.getmangos.eu/bug-tracker/",
                 client: httpx.AsyncClient | None = None):
        self._base = base_url.rstrip("/")
        self._tracker = bug_tracker_url
        # caller should inject a managed client (async with ...) in production
        self._client = client or httpx.AsyncClient()
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    async def list_bug_urls(self) -> list[str]:
        resp = await self._client.post(
            self._base + "/v1/map",
            json={"url": self._tracker},
            headers=self._headers,
        )
        resp.raise_for_status()
        links = resp.json().get("links", [])
        return [u for u in links if _BUG_URL_RE.search(u)]

    async def fetch_bug(self, url: str) -> str:
        resp = await self._client.post(
            self._base + "/v1/scrape",
            json={"url": url, "formats": ["markdown"], "onlyMainContent": True},
            headers=self._headers,
        )
        resp.raise_for_status()
        return resp.json().get("data", {}).get("markdown", "")
