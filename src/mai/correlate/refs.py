import re

from sqlalchemy.ext.asyncio import AsyncSession

from mai.repository.correlation import CorrelationRepository
from mai.repository.reports import ReportRepository

_REF_RE = re.compile(
    r"github\.com/([\w.-]+/[\w.-]+)/(pull|issues)/(\d+)", re.IGNORECASE)
_KIND = {"pull": "gh_pr", "issues": "gh_issue"}


async def correlate_explicit(session: AsyncSession) -> int:
    """Link reports to GitHub PRs/issues they textually reference (if we have them)."""
    crepo = CorrelationRepository(session)
    reports = await ReportRepository(session).all_reports()
    edges = 0
    for report in reports:
        text = await crepo.report_text(report)
        seen = set()
        for full_name, kind, num in _REF_RE.findall(text):
            key = f"{_KIND[kind.lower()]}:{full_name}#{num}"
            if key in seen:
                continue
            seen.add(key)
            related = await crepo.find_report_by_key(key)
            if related is not None and related.id != report.id:
                await crepo.upsert(report.id, related.id, "explicit_ref", 1.0)
                edges += 1
    return edges
