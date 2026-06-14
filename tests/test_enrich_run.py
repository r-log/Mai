from sqlalchemy import func, select

from mai.contracts import IntakeEvent
from mai.db.models import Enrichment, Event
from mai.enrich.fake import FakeEnricher
from mai.enrich.schema import EnrichmentResult
from mai.enrich_run import enrich_pending, enrich_report
from mai.ingest import ingest_event
from mai.repository.reports import ReportRepository

EVT = IntakeEvent("ips", "r1842", "Pet bug", "zero", status="new",
                  raw_payload={"markdown": "threat bug"})


async def test_enrich_report_creates_enrichment_and_event(session):
    await ingest_event(session, EVT)
    await session.commit()
    report = await ReportRepository(session).get_report("ips:r1842")
    enricher = FakeEnricher(EnrichmentResult(
        normalized_title="Pet threat", english_summary="Pet loses threat",
        needs_human_review=True))
    created = await enrich_report(session, enricher, report)
    await session.commit()
    assert created is True
    assert enricher.calls == 1
    row = await session.scalar(select(Enrichment))
    assert row.result["normalized_title"] == "Pet threat"
    assert row.needs_human_review is True
    assert await session.scalar(
        select(func.count()).select_from(Event).where(Event.kind == "enriched")) == 1


async def test_enrich_report_is_cached_on_second_call(session):
    await ingest_event(session, EVT)
    await session.commit()
    report = await ReportRepository(session).get_report("ips:r1842")
    enricher = FakeEnricher()
    assert await enrich_report(session, enricher, report) is True
    await session.commit()
    assert await enrich_report(session, enricher, report) is False
    assert enricher.calls == 1


async def test_enrich_pending_enriches_all_then_none(session):
    await ingest_event(session, EVT)
    await ingest_event(session, IntakeEvent("ips", "r2", "Other", "two", status="new",
                                            raw_payload={"markdown": "x"}))
    await session.commit()
    enricher = FakeEnricher()
    assert await enrich_pending(session, enricher) == 2
    assert await enrich_pending(session, enricher) == 0
