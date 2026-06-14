from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from mai.db.models import (
    Correlation, Report, ReportSourceMap, SourceRecord, Verification,
)
from mai.enrich.schema import raw_text_from_payload


class CorrelationRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def report_text(self, report: Report) -> str:
        """Latest raw text of the report's first mapped source."""
        maps = list(await self._session.scalars(
            select(ReportSourceMap).where(ReportSourceMap.report_id == report.id)
        ))
        for m in maps:
            rec = await self._session.scalar(
                select(SourceRecord)
                .where(SourceRecord.source_type == m.source_type,
                       SourceRecord.source_id == m.source_id)
                .order_by(desc(SourceRecord.version))
                .limit(1)
            )
            if rec is not None:
                return raw_text_from_payload(rec.source_type, rec.payload)
        return ""

    async def find_report_by_key(self, canonical_key: str) -> Report | None:
        return await self._session.scalar(
            select(Report).where(Report.canonical_key == canonical_key)
        )

    async def upsert(self, report_id: str, related_report_id: str,
                     method: str, score: float) -> None:
        existing = await self._session.scalar(
            select(Correlation).where(
                Correlation.report_id == report_id,
                Correlation.related_report_id == related_report_id,
                Correlation.method == method,
            )
        )
        if existing:
            existing.score = score
        else:
            self._session.add(Correlation(
                report_id=report_id, related_report_id=related_report_id,
                method=method, score=score))

    async def for_report(self, report_id: str) -> list[Correlation]:
        return list(await self._session.scalars(
            select(Correlation).where(Correlation.report_id == report_id)
        ))


class VerificationRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def upsert(self, report_id: str, verdict: str, confidence: float,
                     evidence: list) -> None:
        existing = await self._session.scalar(
            select(Verification).where(Verification.report_id == report_id)
        )
        if existing:
            existing.verdict = verdict
            existing.confidence = confidence
            existing.evidence = evidence
        else:
            self._session.add(Verification(
                report_id=report_id, verdict=verdict,
                confidence=confidence, evidence=evidence))

    async def get(self, report_id: str) -> Verification | None:
        return await self._session.scalar(
            select(Verification).where(Verification.report_id == report_id)
        )
