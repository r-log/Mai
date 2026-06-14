import hashlib

from sqlalchemy.ext.asyncio import AsyncSession

from mai.db.models import Report
from mai.embed.embedder import Embedder
from mai.embed.similarity import cosine
from mai.repository.embeddings import EmbeddingRepository
from mai.repository.reports import ReportRepository


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


async def embed_report(session: AsyncSession, embedder: Embedder, report: Report) -> bool:
    """Embed one report's text unless an identical embedding exists. True if created."""
    repo = EmbeddingRepository(session)
    text = await repo.build_text(report)
    input_hash = _hash(text)
    if await repo.exists(report.id, embedder.model, input_hash):
        return False
    vector = await embedder.embed(text)
    repo.add(report_id=report.id, model=embedder.model, dimensions=embedder.dimensions,
             input_hash=input_hash, vector=vector)
    return True


async def embed_pending(session: AsyncSession, embedder: Embedder) -> int:
    """Embed every report lacking a current embedding. Commits per write (resumable)."""
    reports = await ReportRepository(session).all_reports()
    count = 0
    for report in reports:
        if await embed_report(session, embedder, report):
            count += 1
            await session.commit()
    return count


async def most_similar(session: AsyncSession, model: str, query_vector: list[float],
                       top_k: int = 5, exclude_report_id: str | None = None
                       ) -> list[tuple[str, float]]:
    """Rank stored vectors of `model` by cosine similarity to query_vector."""
    pairs = await EmbeddingRepository(session).all_with_vectors(model)
    scored = [(rid, cosine(query_vector, vec))
              for rid, vec in pairs if rid != exclude_report_id]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:top_k]
