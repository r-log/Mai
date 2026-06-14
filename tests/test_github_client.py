import httpx

from mai.github.client import HttpGitHubClient


def _handler(request: httpx.Request) -> httpx.Response:
    assert request.headers["Authorization"] == "Bearer t0ken"
    if request.url.path == "/repos/mangoszero/server/issues":
        return httpx.Response(200, json=[
            {"number": 1, "title": "A", "state": "open", "updated_at": "2026-01-01T00:00:00Z"},
            {"number": 2, "title": "B", "state": "closed",
             "updated_at": "2026-01-02T00:00:00Z", "pull_request": {"url": "x"}},
        ])
    if request.url.path == "/repos/mangoszero/server/pulls":
        return httpx.Response(200, json=[
            {"number": 10, "title": "P", "state": "open", "merged_at": None,
             "updated_at": "2026-02-01T00:00:00Z"},
        ])
    return httpx.Response(404, json={})


async def test_http_client_lists_issues_with_auth_header():
    transport = httpx.MockTransport(_handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = HttpGitHubClient("t0ken", client=http)
        issues = await client.list_issues("mangoszero/server")
    assert [i["number"] for i in issues] == [1, 2]  # raw incl PR; normalize filters later


async def test_http_client_filters_pulls_by_since():
    transport = httpx.MockTransport(_handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = HttpGitHubClient("t0ken", client=http)
        pulls = await client.list_pulls("mangoszero/server", since="2026-03-01T00:00:00Z")
    assert pulls == []  # the only PR (2026-02-01) is older than `since`
