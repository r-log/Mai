from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mai.db.models import SubsystemClass


class SubsystemClassRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get(self, subsystem: str) -> SubsystemClass | None:
        return await self._session.scalar(
            select(SubsystemClass).where(SubsystemClass.subsystem == subsystem)
        )

    async def set_manual(self, subsystem: str, classification: str) -> None:
        existing = await self.get(subsystem)
        if existing:
            existing.classification = classification
            existing.source = "manual_override"
        else:
            self._session.add(SubsystemClass(
                subsystem=subsystem, classification=classification,
                source="manual_override"))

    async def upsert_auto(self, subsystem: str, classification: str,
                          source: str = "heuristic") -> bool:
        """Insert/update an auto classification. Preserve a manual_override row.

        Returns True if written, False if an existing manual_override was kept.
        """
        existing = await self.get(subsystem)
        if existing is not None and existing.source == "manual_override":
            return False
        if existing is not None:
            existing.classification = classification
            existing.source = source
        else:
            self._session.add(SubsystemClass(
                subsystem=subsystem, classification=classification, source=source))
        return True
