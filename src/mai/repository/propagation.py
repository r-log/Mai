from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mai.db.models import PatchGroup, Propagation


class PatchGroupRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_or_create(self, patch_id: str) -> PatchGroup:
        existing = await self._session.scalar(
            select(PatchGroup).where(PatchGroup.patch_id == patch_id)
        )
        if existing:
            return existing
        pg = PatchGroup(patch_id=patch_id)
        self._session.add(pg)
        await self._session.flush()  # populate pg.id
        return pg


class PropagationRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def upsert(self, patch_group_id: str, core: str, *, present: bool,
                     via: str | None, confidence: str, source_sha: str | None,
                     evidence: list) -> None:
        existing = await self._session.scalar(
            select(Propagation).where(
                Propagation.patch_group_id == patch_group_id,
                Propagation.core == core,
            )
        )
        if existing:
            existing.present = present
            existing.via = via
            existing.confidence = confidence
            existing.source_sha = source_sha
            existing.evidence = evidence
        else:
            self._session.add(Propagation(
                patch_group_id=patch_group_id, core=core, present=present, via=via,
                confidence=confidence, source_sha=source_sha, evidence=evidence,
            ))
