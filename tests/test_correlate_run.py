from mai.contracts import IntakeEvent
from mai.correlate.run import correlate_all
from mai.embed.fake import FakeEmbedder
from mai.embed_run import embed_pending
from mai.ingest import ingest_event
from mai.repository.correlation import VerificationRepository
from mai.repository.reports import ReportRepository


async def test_correlate_all_links_and_verifies(session):
    await ingest_event(session, IntakeEvent(
        "ips", "r1", "pet threat broken", "zero",
        raw_payload={"markdown": "fixed by https://github.com/zero/server/pull/7"}))
    await ingest_event(session, IntakeEvent(
        "gh_pr", "zero/server#7", "fix pet threat", "zero", status="merged",
        raw_payload={"body": "fix"}))
    await session.commit()
    embedder = FakeEmbedder()
    await embed_pending(session, embedder)
    await session.commit()
    result = await correlate_all(session, embedder.model, threshold=0.0)
    assert result["explicit_edges"] == 1
    assert result["embedding_edges"] == 1
    assert result["verified"] == 1
    bug = await ReportRepository(session).get_report("ips:r1")
    v = await VerificationRepository(session).get(bug.id)
    assert v.verdict == "fixed_confirmed"  # explicit ref to a merged PR
