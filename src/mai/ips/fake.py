class FakeIpsClient:
    """In-memory IpsClient for tests."""

    def __init__(self, urls: list[str], pages: dict[str, str]):
        self._urls = list(urls)
        self._pages = dict(pages)

    async def list_bug_urls(self) -> list[str]:
        return list(self._urls)

    async def fetch_bug(self, url: str) -> str:
        return self._pages[url]
