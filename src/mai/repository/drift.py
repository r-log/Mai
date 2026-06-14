from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mai.db.models import DriftObservation

_FIELDS = ("shared", "diverged", "identical", "only_a", "only_b")


class DriftRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def upsert(self, fork_a: str, fork_b: str, subsystem: str,
                     stats: dict[str, int]) -> None:
        existing = await self._session.scalar(
            select(DriftObservation).where(
                DriftObservation.fork_a == fork_a,
                DriftObservation.fork_b == fork_b,
                DriftObservation.subsystem == subsystem,
            )
        )
        if existing:
            for field in _FIELDS:
                setattr(existing, field, stats[field])
        else:
            self._session.add(DriftObservation(
                fork_a=fork_a, fork_b=fork_b, subsystem=subsystem,
                **{field: stats[field] for field in _FIELDS}))

    async def for_pair(self, fork_a: str, fork_b: str) -> list[DriftObservation]:
        return list(await self._session.scalars(
            select(DriftObservation).where(
                DriftObservation.fork_a == fork_a,
                DriftObservation.fork_b == fork_b,
            )
        ))
