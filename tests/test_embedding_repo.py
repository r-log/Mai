from sqlalchemy import func, select

from mai.contracts import IntakeEvent
from mai.db.models import Embedding
from mai.enrich.fake import FakeEnricher
from mai.enrich.schema import EnrichmentResult
from mai.enrich_run import enrich_report
from mai.ingest import ingest_event
from mai.repository.embeddings import EmbeddingRepository
from mai.repository.reports import ReportRepository

EVT = IntakeEvent("ips", "r1842", "Pet bug", "zero", status="new",
                  raw_payload={"markdown": "threat bug"})


async def test_build_text_prefers_enrichment_summary(session):
    await ingest_event(session, EVT)
    await session.commit()
    report = await ReportRepository(session).get_report("ips:r1842")
    enricher = FakeEnricher(EnrichmentResult(
        normalized_title="Pet threat", english_summary="Pet loses threat after attack."))
    await enrich_report(session, enricher, report)
    await session.commit()
    text = await EmbeddingRepository(session).build_text(report)
    assert "Pet threat" in text
    assert "Pet loses threat after attack." in text


async def test_build_text_falls_back_to_title_when_not_enriched(session):
    await ingest_event(session, EVT)
    await session.commit()
    report = await ReportRepository(session).get_report("ips:r1842")
    text = await EmbeddingRepository(session).build_text(report)
    assert text == "Pet bug"


async def test_exists_false_then_add_then_true_and_all_with_vectors(session):
    await ingest_event(session, EVT)
    await session.commit()
    report = await ReportRepository(session).get_report("ips:r1842")
    repo = EmbeddingRepository(session)
    assert await repo.exists(report.id, "fake-embed", "h") is False
    repo.add(report_id=report.id, model="fake-embed", dimensions=3,
             input_hash="h", vector=[0.1, 0.2, 0.3])
    await session.commit()
    assert await repo.exists(report.id, "fake-embed", "h") is True
    assert await session.scalar(select(func.count()).select_from(Embedding)) == 1
    pairs = await repo.all_with_vectors("fake-embed")
    assert pairs == [(report.id, [0.1, 0.2, 0.3])]
