from typing import Protocol


class TreeClient(Protocol):
    async def get_tree(self, repo: str) -> dict[str, str]: ...
