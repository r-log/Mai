from mai.contracts import IntakeEvent
from mai.correlate.refs import correlate_explicit
from mai.ingest import ingest_event
from mai.repository.correlation import CorrelationRepository
from mai.repository.reports import ReportRepository


async def test_correlate_explicit_links_ips_bug_to_referenced_pr(session):
    await ingest_event(session, IntakeEvent(
        "ips", "r1", "Pet bug", "zero",
        raw_payload={"markdown": "Looks fixed by https://github.com/zero/server/pull/7 now"}))
    await ingest_event(session, IntakeEvent(
        "gh_pr", "zero/server#7", "Fix pet", "zero", status="merged",
        raw_payload={"body": "x"}))
    await session.commit()
    n = await correlate_explicit(session)
    await session.commit()
    assert n == 1
    rr = ReportRepository(session)
    bug = await rr.get_report("ips:r1")
    pr = await rr.get_report("gh_pr:zero/server#7")
    edges = await CorrelationRepository(session).for_report(bug.id)
    assert len(edges) == 1
    assert edges[0].related_report_id == pr.id
    assert edges[0].method == "explicit_ref"
    assert edges[0].score == 1.0


async def test_correlate_explicit_ignores_refs_to_unknown_reports(session):
    await ingest_event(session, IntakeEvent(
        "ips", "r2", "Bug", "zero",
        raw_payload={"markdown": "see https://github.com/zero/server/pull/999"}))
    await session.commit()
    assert await correlate_explicit(session) == 0  # PR #999 not in our DB
