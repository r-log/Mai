from sqlalchemy.ext.asyncio import AsyncSession

from mai.db.models import Report
from mai.repository.correlation import CorrelationRepository, VerificationRepository
from mai.repository.reports import ReportRepository

VERDICT_OPEN = "open"
VERDICT_LIKELY = "likely_fixed"
VERDICT_CONFIRMED = "fixed_confirmed"

_EMBEDDING_LIKELY_THRESHOLD = 0.7  # min cosine for an embedding edge to a merged PR to imply likely_fixed

_BUG_PREFIXES = ("ips:", "gh_issue:")


async def verify_report(session: AsyncSession, report: Report) -> str:
    crepo = CorrelationRepository(session)
    rrepo = ReportRepository(session)
    corrs = await crepo.for_report(report.id)
    verdict, confidence, evidence = VERDICT_OPEN, 0.1, []
    for c in corrs:
        related = await rrepo.get_by_id(c.related_report_id)
        merged = related is not None and related.status == "merged"
        evidence.append({
            "related": related.canonical_key if related else c.related_report_id,
            "method": c.method, "score": c.score, "merged": merged,
        })
        if c.method == "explicit_ref" and merged:
            verdict, confidence = VERDICT_CONFIRMED, max(confidence, 0.95)
        elif c.method == "explicit_ref":
            if verdict != VERDICT_CONFIRMED:
                verdict = VERDICT_LIKELY
            confidence = max(confidence, 0.7)
        elif c.method == "embedding" and merged and c.score >= _EMBEDDING_LIKELY_THRESHOLD:
            if verdict == VERDICT_OPEN:
                verdict = VERDICT_LIKELY
            confidence = max(confidence, c.score)
    await VerificationRepository(session).upsert(report.id, verdict, confidence, evidence)
    return verdict


async def verify_all(session: AsyncSession) -> int:
    reports = await ReportRepository(session).all_reports()
    count = 0
    for report in reports:
        if report.canonical_key.startswith(_BUG_PREFIXES):
            await verify_report(session, report)
            count += 1
    return count
