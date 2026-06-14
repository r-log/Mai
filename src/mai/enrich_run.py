from sqlalchemy.ext.asyncio import AsyncSession

from mai.db.models import Report
from mai.enrich.enricher import Enricher
from mai.enrich.prompt import PROMPT_VERSION
from mai.enrich.schema import SCHEMA_VERSION
from mai.repository.enrichment import EnrichmentRepository
from mai.repository.reports import ReportRepository


async def enrich_report(session: AsyncSession, enricher: Enricher, report: Report) -> bool:
    """Enrich one report unless an identical enrichment already exists. Returns True if created."""
    repo = EnrichmentRepository(session)
    ctx = await repo.build_input(report)
    input_hash = ctx.content_hash()
    if await repo.exists(report.id, enricher.model, PROMPT_VERSION, SCHEMA_VERSION, input_hash):
        return False
    result = await enricher.enrich(ctx)
    repo.add(report_id=report.id, model=enricher.model, prompt_version=PROMPT_VERSION,
             schema_version=SCHEMA_VERSION, input_hash=input_hash,
             result=result.model_dump(), needs_human_review=result.needs_human_review)
    repo.add_event(report_id=report.id, kind="enriched",
                   payload={"model": enricher.model, "prompt_version": PROMPT_VERSION})
    return True


async def enrich_pending(session: AsyncSession, enricher: Enricher) -> int:
    """Enrich every report lacking a current enrichment. Commits per report (resumable)."""
    reports = await ReportRepository(session).all_reports()
    count = 0
    for report in reports:
        if await enrich_report(session, enricher, report):
            count += 1
        await session.commit()
    return count
