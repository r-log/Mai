import httpx

from mai.ips.client import FirecrawlIpsClient

BUG = "https://www.getmangos.eu/bug-tracker/mangos-zero/agro-x-r1842/"


def _handler(request: httpx.Request) -> httpx.Response:
    assert request.headers["Authorization"] == "Bearer fc-key"
    if request.url.path == "/v1/map":
        return httpx.Response(200, json={"success": True, "links": [
            BUG,
            "https://www.getmangos.eu/profile/1-someone/",
            "https://www.getmangos.eu/bug-tracker/mangos-zero/",
        ]})
    if request.url.path == "/v1/scrape":
        return httpx.Response(200, json={"success": True,
                                         "data": {"markdown": "# T\n\nStatus: New\n"}})
    return httpx.Response(404, json={})


async def test_list_bug_urls_filters_to_bug_pages():
    transport = httpx.MockTransport(_handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = FirecrawlIpsClient("fc-key", client=http)
        urls = await client.list_bug_urls()
    assert urls == [BUG]  # profile + category links filtered out


async def test_fetch_bug_returns_markdown():
    transport = httpx.MockTransport(_handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = FirecrawlIpsClient("fc-key", client=http)
        md = await client.fetch_bug(BUG)
    assert md.startswith("# T")
