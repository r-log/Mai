import asyncio

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from mai.db.models import Report
from mai.enrich.enricher import Enricher
from mai.enrich.prompt import PROMPT_VERSION
from mai.enrich.schema import SCHEMA_VERSION, EnrichmentSchemaError
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
    repo = ReportRepository(session)
    report_ids = [r.id for r in await repo.all_reports()]
    count = 0
    for report_id in report_ids:
        report = await session.get(Report, report_id)
        if report is None:
            continue
        try:
            if await enrich_report(session, enricher, report):
                count += 1
                await session.commit()
        except (EnrichmentSchemaError, httpx.HTTPError):
            await session.rollback()
            continue
    return count


async def _enrichment_plan(session: AsyncSession, enricher: Enricher) -> list[tuple]:
    """Sequentially build the list of (report_id, ctx, input_hash) still needing work.

    All session access happens here, single-threaded, before any concurrent calls.
    """
    reports = ReportRepository(session)
    repo = EnrichmentRepository(session)
    plan: list[tuple] = []
    for stub in await reports.all_reports():
        report = await session.get(Report, stub.id)
        if report is None:
            continue
        ctx = await repo.build_input(report)
        input_hash = ctx.content_hash()
        if await repo.exists(report.id, enricher.model, PROMPT_VERSION,
                             SCHEMA_VERSION, input_hash):
            continue
        plan.append((report.id, ctx, input_hash))
    return plan


async def enrich_pending_concurrent(session: AsyncSession, enricher: Enricher,
                                    concurrency: int = 8) -> int:
    """Enrich every pending report, running LLM calls concurrently.

    The model network calls fan out up to `concurrency` at a time; every DB write
    is serialized under one lock so the shared session/SQLite stays single-threaded.
    Commits per report, so the run is resumable.
    """
    plan = await _enrichment_plan(session, enricher)
    repo = EnrichmentRepository(session)
    sem = asyncio.Semaphore(concurrency)
    db_lock = asyncio.Lock()
    count = 0

    async def worker(report_id: str, ctx, input_hash: str) -> None:
        nonlocal count
        async with sem:
            try:
                result = await enricher.enrich(ctx)
            except (EnrichmentSchemaError, httpx.HTTPError):
                # Bad model output or a transient HTTP error: skip this report.
                # It stays pending and is picked up on the next run.
                return
        async with db_lock:
            repo.add(report_id=report_id, model=enricher.model,
                     prompt_version=PROMPT_VERSION, schema_version=SCHEMA_VERSION,
                     input_hash=input_hash, result=result.model_dump(),
                     needs_human_review=result.needs_human_review)
            repo.add_event(report_id=report_id, kind="enriched",
                           payload={"model": enricher.model, "prompt_version": PROMPT_VERSION})
            await session.commit()
            count += 1

    await asyncio.gather(*(worker(*item) for item in plan))
    return count
