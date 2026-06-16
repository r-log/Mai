import asyncio

import httpx

from mai.contracts import IntakeEvent
from mai.enrich.schema import EnrichmentResult, EnrichmentSchemaError
from mai.enrich_run import enrich_pending_concurrent
from mai.ingest import ingest_event


def _evt(sid: str, core: str = "zero") -> IntakeEvent:
    return IntakeEvent("ips", sid, sid.upper(), core, raw_payload={"markdown": sid})


class _CountingEnricher:
    def __init__(self):
        self.calls = 0
        self.model = "fake"

    async def enrich(self, ctx):
        self.calls += 1
        return EnrichmentResult(normalized_title="n", english_summary="s")


class _FlakyEnricher:
    """Fails exactly one report; succeeds on the rest."""
    def __init__(self):
        self._failed = False
        self.model = "flaky"

    async def enrich(self, ctx):
        if not self._failed:
            self._failed = True
            raise EnrichmentSchemaError("null content")
        return EnrichmentResult(normalized_title="ok", english_summary="ok")


class _PeakTracker:
    """Records the peak number of concurrently in-flight enrich calls."""
    def __init__(self):
        self.inflight = 0
        self.peak = 0
        self.model = "peak"

    async def enrich(self, ctx):
        self.inflight += 1
        self.peak = max(self.peak, self.inflight)
        await asyncio.sleep(0.02)  # hold the slot so overlap is observable
        self.inflight -= 1
        return EnrichmentResult(normalized_title="n", english_summary="s")


async def test_concurrent_enriches_all_then_none(session):
    for sid in ("r1", "r2", "r3"):
        await ingest_event(session, _evt(sid))
    await session.commit()
    enr = _CountingEnricher()
    assert await enrich_pending_concurrent(session, enr, concurrency=4) == 3
    assert enr.calls == 3
    assert await enrich_pending_concurrent(session, enr, concurrency=4) == 0


async def test_concurrent_skips_schema_errors(session):
    await ingest_event(session, _evt("r1"))
    await ingest_event(session, _evt("r2"))
    await session.commit()
    assert await enrich_pending_concurrent(session, _FlakyEnricher(), concurrency=4) == 1


class _HttpErrorEnricher:
    """Raises a transient HTTP error on the first report, succeeds after."""
    def __init__(self):
        self._failed = False
        self.model = "httperr"

    async def enrich(self, ctx):
        if not self._failed:
            self._failed = True
            raise httpx.HTTPStatusError(
                "429", request=httpx.Request("POST", "http://x"),
                response=httpx.Response(429))
        return EnrichmentResult(normalized_title="ok", english_summary="ok")


async def test_concurrent_skips_http_errors(session):
    await ingest_event(session, _evt("r1"))
    await ingest_event(session, _evt("r2"))
    await session.commit()
    # one report hits a transient HTTP error -> skipped, the batch is not aborted
    assert await enrich_pending_concurrent(session, _HttpErrorEnricher(), concurrency=4) == 1


async def test_concurrent_respects_bound(session):
    for i in range(6):
        await ingest_event(session, _evt(f"r{i}"))
    await session.commit()
    tracker = _PeakTracker()
    await enrich_pending_concurrent(session, tracker, concurrency=2)
    assert tracker.peak <= 2
    assert tracker.peak >= 2  # with 6 items and a held slot, the bound is actually reached
