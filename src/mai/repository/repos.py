from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mai.db.models import Repo


class RepoRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def upsert(self, full_name: str, core: str, url: str) -> Repo:
        existing = await self._session.scalar(
            select(Repo).where(Repo.full_name == full_name)
        )
        if existing:
            existing.core, existing.url = core, url
            return existing
        repo = Repo(full_name=full_name, core=core, url=url)
        self._session.add(repo)
        return repo

    async def all(self) -> list[Repo]:
        return list(await self._session.scalars(select(Repo).order_by(Repo.full_name)))
