import httpx

from mai.drift.client import GitHubTreeClient


def _handler(request: httpx.Request) -> httpx.Response:
    assert request.headers["Authorization"] == "Bearer t0ken"
    assert request.url.path == "/repos/mangoszero/server/git/trees/HEAD"
    assert request.url.params.get("recursive") == "1"
    return httpx.Response(200, json={
        "sha": "root",
        "tree": [
            {"path": "src/game/Object/Player.cpp", "type": "blob", "sha": "aaa"},
            {"path": "src/game/Object", "type": "tree", "sha": "bbb"},  # dir -> skipped
            {"path": "README.md", "type": "blob", "sha": "ccc"},
        ],
        "truncated": False,
    })


async def test_tree_client_returns_only_blob_paths():
    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as http:
        client = GitHubTreeClient("t0ken", client=http)
        tree = await client.get_tree("mangoszero/server")
    assert tree == {"src/game/Object/Player.cpp": "aaa", "README.md": "ccc"}
