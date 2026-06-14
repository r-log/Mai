from mai.contracts import IntakeEvent
from mai.enrich.schema import EnrichmentResult, EnrichmentSchemaError
from mai.enrich_run import enrich_pending
from mai.ingest import ingest_event


class _FlakyEnricher:
    """Fails the first report with EnrichmentSchemaError, succeeds on the rest."""
    def __init__(self):
        self._seen = 0
        self.model = "flaky"

    async def enrich(self, ctx):
        self._seen += 1
        if self._seen == 1:
            raise EnrichmentSchemaError("null content")
        return EnrichmentResult(normalized_title="ok", english_summary="ok")


async def test_enrich_pending_skips_failures_and_continues(session):
    await ingest_event(session, IntakeEvent("ips", "r1", "A", "zero", raw_payload={"markdown": "a"}))
    await ingest_event(session, IntakeEvent("ips", "r2", "B", "zero", raw_payload={"markdown": "b"}))
    await session.commit()
    # one report fails, the other succeeds -> returns 1, no crash
    assert await enrich_pending(session, _FlakyEnricher()) == 1
