from mai.contracts import IntakeEvent
from mai.ingest import ingest_event
from mai.repository.correlation import CorrelationRepository, VerificationRepository
from mai.repository.reports import ReportRepository


async def _two_reports(session):
    await ingest_event(session, IntakeEvent(
        "ips", "r1", "Pet bug", "zero",
        raw_payload={"markdown": "broken; fixed in https://github.com/zero/server/pull/7"}))
    await ingest_event(session, IntakeEvent(
        "gh_pr", "zero/server#7", "Fix pet", "zero", status="merged",
        raw_payload={"body": "fixes pet threat"}))
    await session.commit()
    rr = ReportRepository(session)
    return await rr.get_report("ips:r1"), await rr.get_report("gh_pr:zero/server#7")


async def test_report_text_and_find_by_key(session):
    bug, pr = await _two_reports(session)
    repo = CorrelationRepository(session)
    assert "github.com/zero/server/pull/7" in await repo.report_text(bug)
    found = await repo.find_report_by_key("gh_pr:zero/server#7")
    assert found is not None and found.id == pr.id


async def test_correlation_upsert_is_idempotent(session):
    bug, pr = await _two_reports(session)
    repo = CorrelationRepository(session)
    await repo.upsert(bug.id, pr.id, "explicit_ref", 1.0)
    await repo.upsert(bug.id, pr.id, "explicit_ref", 0.9)  # same key -> update, no dup
    await session.commit()
    edges = await repo.for_report(bug.id)
    assert len(edges) == 1
    assert edges[0].score == 0.9


async def test_verification_upsert_keeps_one_row(session):
    bug, _ = await _two_reports(session)
    vrepo = VerificationRepository(session)
    await vrepo.upsert(bug.id, "open", 0.1, [])
    await vrepo.upsert(bug.id, "fixed_confirmed", 0.95, [{"x": 1}])
    await session.commit()
    v = await vrepo.get(bug.id)
    assert v.verdict == "fixed_confirmed"
    assert v.confidence == 0.95
    assert await ReportRepository(session).get_by_id(bug.id) is not None
