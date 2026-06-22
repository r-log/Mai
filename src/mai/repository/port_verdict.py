from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mai.db.models import PortVerdict


class PortVerdictRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get(self, pg_id: str, core: str) -> PortVerdict | None:
        return await self._session.scalar(
            select(PortVerdict).where(PortVerdict.patch_group_id == pg_id,
                                      PortVerdict.core == core))

    async def upsert(self, pg_id: str, core: str, **fields) -> PortVerdict:
        existing = await self.get(pg_id, core)
        if existing is not None:
            for k, v in fields.items():
                setattr(existing, k, v)
            return existing
        v = PortVerdict(patch_group_id=pg_id, core=core, **fields)
        self._session.add(v)
        return v

    async def actionable(self) -> list[PortVerdict]:
        return list(await self._session.scalars(
            select(PortVerdict).where(PortVerdict.verdict.in_(("needs", "review")))))

    async def for_fix(self, pg_id: str) -> list[PortVerdict]:
        return list(await self._session.scalars(
            select(PortVerdict).where(PortVerdict.patch_group_id == pg_id)))
