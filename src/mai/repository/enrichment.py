from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from mai.db.models import Enrichment, Event, Report, ReportSourceMap, SourceRecord
from mai.enrich.schema import EnrichmentInput, raw_text_from_payload


class EnrichmentRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def build_input(self, report: Report) -> EnrichmentInput:
        """Build the model input from the report's latest raw source record."""
        maps = list(await self._session.scalars(
            select(ReportSourceMap).where(ReportSourceMap.report_id == report.id)
        ))
        source_type = ""
        raw_text = ""
        for m in maps:
            rec = await self._session.scalar(
                select(SourceRecord)
                .where(SourceRecord.source_type == m.source_type,
                       SourceRecord.source_id == m.source_id)
                .order_by(desc(SourceRecord.version))
                .limit(1)
            )
            if rec is not None:
                source_type = rec.source_type
                raw_text = raw_text_from_payload(rec.source_type, rec.payload)
                break
        return EnrichmentInput(title=report.title, core=report.core,
                               source_type=source_type, raw_text=raw_text)

    async def exists(self, report_id: str, model: str, prompt_version: int,
                     schema_version: int, input_hash: str) -> bool:
        return bool(await self._session.scalar(
            select(Enrichment.id).where(
                Enrichment.report_id == report_id,
                Enrichment.model == model,
                Enrichment.prompt_version == prompt_version,
                Enrichment.schema_version == schema_version,
                Enrichment.input_hash == input_hash,
            )
        ))

    def add(self, **kw) -> Enrichment:
        row = Enrichment(**kw)
        self._session.add(row)
        return row

    def add_event(self, **kw) -> None:
        self._session.add(Event(**kw))
