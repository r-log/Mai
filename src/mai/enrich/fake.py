from mai.enrich.schema import EnrichmentInput, EnrichmentResult


class FakeEnricher:
    """Deterministic Enricher for tests. Counts calls so caching can be asserted."""

    def __init__(self, result: EnrichmentResult | None = None, model: str = "fake"):
        self._result = result or EnrichmentResult(
            normalized_title="Norm", english_summary="Sum")
        self._model = model
        self.calls = 0

    @property
    def model(self) -> str:
        return self._model

    async def enrich(self, ctx: EnrichmentInput) -> EnrichmentResult:
        self.calls += 1
        return self._result
