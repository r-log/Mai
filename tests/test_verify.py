from mai.contracts import IntakeEvent
from mai.correlate.verify import (
    VERDICT_CONFIRMED, VERDICT_LIKELY, VERDICT_OPEN, verify_all,
)
from mai.ingest import ingest_event
from mai.repository.correlation import CorrelationRepository, VerificationRepository
from mai.repository.reports import ReportRepository


async def _bug_and_pr(session, pr_status):
    await ingest_event(session, IntakeEvent("ips", "r1", "Bug", "zero",
                                            raw_payload={"markdown": "x"}))
    await ingest_event(session, IntakeEvent("gh_pr", "zero/server#7", "Fix", "zero",
                                            status=pr_status, raw_payload={"body": "y"}))
    await session.commit()
    rr = ReportRepository(session)
    return await rr.get_report("ips:r1"), await rr.get_report("gh_pr:zero/server#7")


async def test_explicit_ref_to_merged_pr_is_confirmed(session):
    bug, pr = await _bug_and_pr(session, "merged")
    await CorrelationRepository(session).upsert(bug.id, pr.id, "explicit_ref", 1.0)
    await session.commit()
    await verify_all(session)
    await session.commit()
    v = await VerificationRepository(session).get(bug.id)
    assert v.verdict == VERDICT_CONFIRMED
    assert v.confidence == 0.95
    assert v.evidence[0]["related"] == "gh_pr:zero/server#7"


async def test_explicit_ref_to_open_pr_is_likely(session):
    bug, pr = await _bug_and_pr(session, "open")
    await CorrelationRepository(session).upsert(bug.id, pr.id, "explicit_ref", 1.0)
    await session.commit()
    await verify_all(session)
    await session.commit()
    v = await VerificationRepository(session).get(bug.id)
    assert v.verdict == VERDICT_LIKELY


async def test_no_correlations_is_open(session):
    bug, _ = await _bug_and_pr(session, "merged")
    await verify_all(session)
    await session.commit()
    v = await VerificationRepository(session).get(bug.id)
    assert v.verdict == VERDICT_OPEN


async def test_gh_issue_report_is_verified(session):
    await ingest_event(session, IntakeEvent("gh_issue", "zero/server#5", "Issue", "zero",
                                            raw_payload={"body": "x"}))
    await ingest_event(session, IntakeEvent("gh_pr", "zero/server#7", "Fix", "zero",
                                            status="merged", raw_payload={"body": "y"}))
    await session.commit()
    rr = ReportRepository(session)
    issue = await rr.get_report("gh_issue:zero/server#5")
    pr = await rr.get_report("gh_pr:zero/server#7")
    await CorrelationRepository(session).upsert(issue.id, pr.id, "explicit_ref", 1.0)
    await session.commit()
    await verify_all(session)
    await session.commit()
    v = await VerificationRepository(session).get(issue.id)
    assert v.verdict == VERDICT_CONFIRMED
