import hashlib
import json

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from mai.db.models import Event, Report, ReportSourceMap, SourceRecord


def content_hash(payload: dict) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


class ReportRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def latest_source_version(self, source_type: str, source_id: str) -> int:
        row = await self._session.scalar(
            select(SourceRecord.version)
            .where(SourceRecord.source_type == source_type,
                   SourceRecord.source_id == source_id)
            .order_by(desc(SourceRecord.version))
            .limit(1)
        )
        return row or 0

    async def source_exists(self, source_type: str, source_id: str, chash: str) -> bool:
        return bool(await self._session.scalar(
            select(SourceRecord.id).where(
                SourceRecord.source_type == source_type,
                SourceRecord.source_id == source_id,
                SourceRecord.content_hash == chash,
            )
        ))

    def add_source_record(self, **kw) -> SourceRecord:
        rec = SourceRecord(**kw)
        self._session.add(rec)
        return rec

    async def get_report(self, canonical_key: str) -> Report | None:
        return await self._session.scalar(
            select(Report).where(Report.canonical_key == canonical_key)
        )

    def add_report(self, **kw) -> Report:
        rep = Report(**kw)
        self._session.add(rep)
        return rep

    async def map_exists(self, source_type: str, source_id: str) -> bool:
        return bool(await self._session.scalar(
            select(ReportSourceMap.id).where(
                ReportSourceMap.source_type == source_type,
                ReportSourceMap.source_id == source_id,
            )
        ))

    def add_map(self, **kw) -> None:
        self._session.add(ReportSourceMap(**kw))

    def add_event(self, **kw) -> None:
        self._session.add(Event(**kw))

    async def all_reports(self) -> list["Report"]:
        return list(await self._session.scalars(
            select(Report).order_by(Report.canonical_key)))

    async def source_keys_for(self, report_id: str) -> list[str]:
        rows = await self._session.scalars(
            select(ReportSourceMap).where(ReportSourceMap.report_id == report_id)
        )
        return [f"{m.source_type}:{m.source_id}" for m in rows]

    async def get_by_id(self, report_id: str) -> "Report | None":
        return await self._session.scalar(select(Report).where(Report.id == report_id))
