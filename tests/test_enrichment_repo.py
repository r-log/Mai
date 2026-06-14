from sqlalchemy import func, select

from mai.contracts import IntakeEvent
from mai.db.models import Enrichment
from mai.enrich.schema import EnrichmentInput
from mai.ingest import ingest_event
from mai.repository.enrichment import EnrichmentRepository
from mai.repository.reports import ReportRepository

EVT = IntakeEvent("ips", "r1842", "Pet bug", "zero", status="new",
                  raw_payload={"markdown": "# Pet\nthreat union bug"})


async def test_build_input_extracts_latest_raw_text(session):
    await ingest_event(session, EVT)
    await session.commit()
    report = await ReportRepository(session).get_report("ips:r1842")
    ctx = await EnrichmentRepository(session).build_input(report)
    assert isinstance(ctx, EnrichmentInput)
    assert ctx.title == "Pet bug"
    assert ctx.core == "zero"
    assert ctx.source_type == "ips"
    assert "threat union bug" in ctx.raw_text


async def test_exists_false_then_add_then_true(session):
    await ingest_event(session, EVT)
    await session.commit()
    report = await ReportRepository(session).get_report("ips:r1842")
    repo = EnrichmentRepository(session)
    assert await repo.exists(report.id, "fake", 1, 1, "hash") is False
    repo.add(report_id=report.id, model="fake", prompt_version=1, schema_version=1,
             input_hash="hash", result={"normalized_title": "x"}, needs_human_review=False)
    await session.commit()
    assert await repo.exists(report.id, "fake", 1, 1, "hash") is True
    assert await session.scalar(select(func.count()).select_from(Enrichment)) == 1
