from sqlalchemy.ext.asyncio import AsyncSession

from mai.embed.similarity import cosine
from mai.repository.correlation import CorrelationRepository
from mai.repository.embeddings import EmbeddingRepository
from mai.repository.reports import ReportRepository


async def correlate_embeddings(session: AsyncSession, model: str,
                               source_prefix: str = "ips:",
                               target_prefix: str = "gh_pr:",
                               top_k: int = 3, threshold: float = 0.5) -> int:
    """Link each source-report's vector to its top_k most similar target vectors."""
    pairs = await EmbeddingRepository(session).all_with_vectors(model)
    reports = {r.id: r for r in await ReportRepository(session).all_reports()}
    sources = [(rid, v) for rid, v in pairs
               if rid in reports and reports[rid].canonical_key.startswith(source_prefix)]
    targets = [(rid, v) for rid, v in pairs
               if rid in reports and reports[rid].canonical_key.startswith(target_prefix)]
    crepo = CorrelationRepository(session)
    edges = 0
    for srid, svec in sources:
        scored = sorted(
            ((trid, cosine(svec, tvec)) for trid, tvec in targets if trid != srid),
            key=lambda pair: pair[1], reverse=True,
        )[:top_k]
        for trid, score in scored:
            if score >= threshold:
                await crepo.upsert(srid, trid, "embedding", score)
                edges += 1
    return edges
