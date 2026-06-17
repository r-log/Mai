from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mai.db.models import PortCandidate


def magnitude_tier(magnitude: int) -> str:
    """Band a candidate's line-magnitude. surgical<=50<small<=500<moderate<=5000<bulk."""
    if magnitude <= 50:
        return "surgical"
    if magnitude <= 500:
        return "small"
    if magnitude <= 5000:
        return "moderate"
    return "bulk"


class PortCandidateRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get(self, patch_group_id: str, target_core: str) -> PortCandidate | None:
        return await self._session.scalar(
            select(PortCandidate).where(
                PortCandidate.patch_group_id == patch_group_id,
                PortCandidate.target_core == target_core,
            )
        )

    async def upsert(self, patch_group_id: str, target_core: str, *, source_core: str,
                     subsystem: str, classification: str, magnitude: int,
                     confidence: str, evidence: list, source_sha: str | None) -> None:
        """Insert a new open candidate, or update computed fields preserving `status`."""
        existing = await self.get(patch_group_id, target_core)
        if existing is not None:
            existing.source_core = source_core
            existing.subsystem = subsystem
            existing.classification = classification
            existing.magnitude = magnitude
            existing.tier = magnitude_tier(magnitude)
            existing.confidence = confidence
            existing.evidence = evidence
            existing.source_sha = source_sha
        else:
            self._session.add(PortCandidate(
                patch_group_id=patch_group_id, source_core=source_core,
                target_core=target_core, subsystem=subsystem,
                classification=classification, magnitude=magnitude,
                tier=magnitude_tier(magnitude),
                confidence=confidence, evidence=evidence, source_sha=source_sha))

    async def open_candidates(self) -> list[PortCandidate]:
        return list(await self._session.scalars(
            select(PortCandidate).where(PortCandidate.status == "open")
        ))

    async def mark_status(self, candidate: PortCandidate, status: str) -> None:
        candidate.status = status
