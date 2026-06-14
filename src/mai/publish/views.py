from dataclasses import dataclass

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mai.db.models import DriftObservation, Enrichment, Report, Verification
from mai.repository.correlation import CorrelationRepository, VerificationRepository
from mai.repository.reports import ReportRepository

_BUG_PREFIXES = ("ips:", "gh_issue:")


@dataclass
class ReportBundle:
    report: Report
    enrichment: dict | None
    verification: Verification | None
    correlations: list[tuple[str, str, float]]


async def report_bundle(session: AsyncSession, report: Report) -> ReportBundle:
    enr = await session.scalar(
        select(Enrichment).where(Enrichment.report_id == report.id)
        .order_by(desc(Enrichment.created_at)).limit(1)
    )
    ver = await VerificationRepository(session).get(report.id)
    rr = ReportRepository(session)
    corrs = []
    for c in await CorrelationRepository(session).for_report(report.id):
        related = await rr.get_by_id(c.related_report_id)
        key = related.canonical_key if related else c.related_report_id
        corrs.append((key, c.method, c.score))
    return ReportBundle(report=report,
                        enrichment=enr.result if enr else None,
                        verification=ver, correlations=corrs)


async def iter_bug_reports(session: AsyncSession) -> list[Report]:
    reports = await ReportRepository(session).all_reports()
    return [r for r in reports if r.canonical_key.startswith(_BUG_PREFIXES)]


async def drift_observations_by_pair(
        session: AsyncSession) -> dict[tuple[str, str], list[DriftObservation]]:
    grouped: dict[tuple[str, str], list[DriftObservation]] = {}
    for o in await session.scalars(
            select(DriftObservation).order_by(
                DriftObservation.fork_a, DriftObservation.fork_b, DriftObservation.subsystem)):
        grouped.setdefault((o.fork_a, o.fork_b), []).append(o)
    return grouped


async def counts(session: AsyncSession) -> dict:
    async def _count(stmt) -> int:
        return await session.scalar(stmt) or 0

    return {
        "reports": await _count(select(func.count()).select_from(Report)),
        "enriched": await _count(
            select(func.count(func.distinct(Enrichment.report_id)))),
        "open": await _count(select(func.count()).select_from(Verification)
                             .where(Verification.verdict == "open")),
        "likely_fixed": await _count(select(func.count()).select_from(Verification)
                                     .where(Verification.verdict == "likely_fixed")),
        "fixed_confirmed": await _count(select(func.count()).select_from(Verification)
                                        .where(Verification.verdict == "fixed_confirmed")),
        "drift_pairs": len(await drift_observations_by_pair(session)),
    }
