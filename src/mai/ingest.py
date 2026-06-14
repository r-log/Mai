from sqlalchemy.ext.asyncio import AsyncSession

from mai.contracts import IntakeEvent
from mai.repository.reports import ReportRepository, content_hash


async def ingest_event(session: AsyncSession, evt: IntakeEvent) -> None:
    """Normalize one IntakeEvent: append immutable raw, upsert derived report."""
    repo = ReportRepository(session)
    chash = content_hash(evt.raw_payload)

    if await repo.source_exists(evt.source_type, evt.source_id, chash):
        return  # idempotent: identical payload already stored

    next_version = await repo.latest_source_version(evt.source_type, evt.source_id) + 1
    repo.add_source_record(
        source_type=evt.source_type, source_id=evt.source_id,
        repo_full_name=evt.repo_full_name, content_hash=chash,
        version=next_version, payload=evt.raw_payload,
    )

    key = evt.canonical_key()
    report = await repo.get_report(key)
    if report is None:
        report = repo.add_report(
            canonical_key=key, core=evt.core, title=evt.title, status=evt.status,
        )
        await session.flush()
        repo.add_event(report_id=report.id, kind="ingested",
                       payload={"source_id": evt.source_id})
    else:
        if report.status != evt.status:
            repo.add_event(report_id=report.id, kind="status_changed",
                           payload={"from": report.status, "to": evt.status})
            report.status = evt.status

    if not await repo.map_exists(evt.source_type, evt.source_id):
        repo.add_map(report_id=report.id, source_type=evt.source_type,
                     source_id=evt.source_id)
