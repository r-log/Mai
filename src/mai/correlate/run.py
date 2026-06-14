from sqlalchemy.ext.asyncio import AsyncSession

from mai.correlate.embedding import correlate_embeddings
from mai.correlate.refs import correlate_explicit
from mai.correlate.verify import verify_all


async def correlate_all(session: AsyncSession, model: str,
                        threshold: float = 0.5) -> dict:
    """Run both correlators then verification over everything in the DB. Offline."""
    explicit_edges = await correlate_explicit(session)
    embedding_edges = await correlate_embeddings(session, model, threshold=threshold)
    verified = await verify_all(session)
    await session.commit()
    return {"explicit_edges": explicit_edges,
            "embedding_edges": embedding_edges,
            "verified": verified}
