from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from mai.db.models import Embedding, Enrichment, Report


class EmbeddingRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def build_text(self, report: Report) -> str:
        """Embed-text: the latest enrichment's title+summary, else the report title."""
        enr = await self._session.scalar(
            select(Enrichment)
            .where(Enrichment.report_id == report.id)
            .order_by(desc(Enrichment.created_at))
            .limit(1)
        )
        if enr is not None:
            r = enr.result
            text = f"{r.get('normalized_title', '')}\n{r.get('english_summary', '')}".strip()
            if text:
                return text
        return report.title

    async def exists(self, report_id: str, model: str, input_hash: str) -> bool:
        return bool(await self._session.scalar(
            select(Embedding.id).where(
                Embedding.report_id == report_id,
                Embedding.model == model,
                Embedding.input_hash == input_hash,
            )
        ))

    def add(self, **kw) -> Embedding:
        row = Embedding(**kw)
        self._session.add(row)
        return row

    async def all_with_vectors(self, model: str) -> list[tuple[str, list[float]]]:
        rows = await self._session.scalars(
            select(Embedding).where(Embedding.model == model)
        )
        return [(row.report_id, row.vector) for row in rows]
