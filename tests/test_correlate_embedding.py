from mai.contracts import IntakeEvent
from mai.correlate.embedding import correlate_embeddings
from mai.embed.fake import FakeEmbedder
from mai.embed_run import embed_pending
from mai.ingest import ingest_event
from mai.repository.correlation import CorrelationRepository
from mai.repository.reports import ReportRepository


async def test_correlate_embeddings_links_bug_to_pr_candidates(session):
    await ingest_event(session, IntakeEvent("ips", "r1", "pet threat broken", "zero",
                                            raw_payload={"markdown": "pet threat broken"}))
    await ingest_event(session, IntakeEvent("gh_pr", "zero/server#7", "fix pet threat",
                                            "zero", status="merged",
                                            raw_payload={"body": "fix"}))
    await session.commit()
    embedder = FakeEmbedder()
    await embed_pending(session, embedder)
    await session.commit()
    # threshold 0.0 so the single PR candidate is always linked (mechanics test)
    n = await correlate_embeddings(session, embedder.model, top_k=3, threshold=0.0)
    await session.commit()
    assert n == 1
    rr = ReportRepository(session)
    bug = await rr.get_report("ips:r1")
    pr = await rr.get_report("gh_pr:zero/server#7")
    edges = await CorrelationRepository(session).for_report(bug.id)
    assert [(e.related_report_id, e.method) for e in edges] == [(pr.id, "embedding")]
    assert 0.0 <= edges[0].score <= 1.0


async def test_correlate_embeddings_does_not_link_bug_to_itself(session):
    await ingest_event(session, IntakeEvent("ips", "r1", "only a bug", "zero",
                                            raw_payload={"markdown": "only a bug"}))
    await session.commit()
    await embed_pending(session, FakeEmbedder())
    await session.commit()
    # no gh_pr targets -> no edges
    assert await correlate_embeddings(session, "fake-embed", top_k=3, threshold=0.0) == 0
