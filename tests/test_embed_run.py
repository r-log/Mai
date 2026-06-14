from sqlalchemy import func, select

from mai.contracts import IntakeEvent
from mai.db.models import Embedding
from mai.embed.fake import FakeEmbedder
from mai.embed_run import embed_pending, embed_report, most_similar
from mai.ingest import ingest_event
from mai.repository.embeddings import EmbeddingRepository
from mai.repository.reports import ReportRepository


def _evt(rid: str, title: str) -> IntakeEvent:
    return IntakeEvent("ips", rid, title, "zero", status="new",
                       raw_payload={"markdown": title})


async def test_embed_report_creates_then_caches(session):
    await ingest_event(session, _evt("r1", "Pet threat bug"))
    await session.commit()
    report = await ReportRepository(session).get_report("ips:r1")
    embedder = FakeEmbedder()
    assert await embed_report(session, embedder, report) is True
    await session.commit()
    assert await embed_report(session, embedder, report) is False
    assert embedder.calls == 1
    assert await session.scalar(select(func.count()).select_from(Embedding)) == 1


async def test_embed_pending_embeds_all_then_none(session):
    await ingest_event(session, _evt("r1", "Pet bug"))
    await ingest_event(session, _evt("r2", "Mount bug"))
    await session.commit()
    embedder = FakeEmbedder()
    assert await embed_pending(session, embedder) == 2
    assert await embed_pending(session, embedder) == 0


async def test_most_similar_ranks_and_excludes_self(session):
    for rid, title in [("r1", "alpha"), ("r2", "beta"), ("r3", "gamma")]:
        await ingest_event(session, _evt(rid, title))
    await session.commit()
    embedder = FakeEmbedder()
    await embed_pending(session, embedder)
    r1 = await ReportRepository(session).get_report("ips:r1")
    pairs = await EmbeddingRepository(session).all_with_vectors(embedder.model)
    r1_vector = next(v for rid, v in pairs if rid == r1.id)
    ranked = await most_similar(session, embedder.model, r1_vector, top_k=2,
                                exclude_report_id=r1.id)
    assert len(ranked) == 2
    assert r1.id not in [rid for rid, _ in ranked]
    assert ranked[0][1] >= ranked[1][1]  # sorted descending by score
