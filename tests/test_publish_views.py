from mai.contracts import IntakeEvent
from mai.ingest import ingest_event
from mai.publish.views import (
    counts, drift_observations_by_pair, iter_bug_reports, report_bundle,
)
from mai.repository.correlation import CorrelationRepository, VerificationRepository
from mai.repository.drift import DriftRepository
from mai.repository.enrichment import EnrichmentRepository
from mai.repository.reports import ReportRepository

STATS = {"shared": 5, "diverged": 3, "identical": 2, "only_a": 0, "only_b": 1}


async def _seed(session):
    await ingest_event(session, IntakeEvent("ips", "r1", "Pet bug", "zero",
                                            raw_payload={"markdown": "x"}))
    await ingest_event(session, IntakeEvent("gh_pr", "zero/server#7", "Fix", "zero",
                                            status="merged", raw_payload={"body": "y"}))
    await session.commit()
    rr = ReportRepository(session)
    bug = await rr.get_report("ips:r1")
    pr = await rr.get_report("gh_pr:zero/server#7")
    EnrichmentRepository(session).add(
        report_id=bug.id, model="m", prompt_version=1, schema_version=1, input_hash="h",
        result={"normalized_title": "Pet threat", "english_summary": "Pet loses threat."},
        needs_human_review=False)
    await CorrelationRepository(session).upsert(bug.id, pr.id, "explicit_ref", 1.0)
    await VerificationRepository(session).upsert(bug.id, "fixed_confirmed", 0.95, [])
    await DriftRepository(session).upsert("zero/server", "two/server", "src/game/Object", STATS)
    await session.commit()
    return bug


async def test_report_bundle_gathers_everything(session):
    bug = await _seed(session)
    b = await report_bundle(session, bug)
    assert b.enrichment["normalized_title"] == "Pet threat"
    assert b.verification.verdict == "fixed_confirmed"
    assert b.correlations == [("gh_pr:zero/server#7", "explicit_ref", 1.0)]


async def test_iter_bug_reports_excludes_prs(session):
    await _seed(session)
    keys = [r.canonical_key for r in await iter_bug_reports(session)]
    assert keys == ["ips:r1"]  # the gh_pr is not a bug


async def test_drift_observations_grouped_by_pair(session):
    await _seed(session)
    grouped = await drift_observations_by_pair(session)
    assert list(grouped.keys()) == [("zero/server", "two/server")]
    assert grouped[("zero/server", "two/server")][0].diverged == 3


async def test_counts_summarizes_store(session):
    await _seed(session)
    c = await counts(session)
    assert c["reports"] == 2
    assert c["enriched"] == 1
    assert c["fixed_confirmed"] == 1
    assert c["open"] == 0
    assert c["drift_pairs"] == 1
