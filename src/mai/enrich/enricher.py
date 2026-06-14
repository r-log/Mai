from typing import Protocol

from mai.enrich.schema import EnrichmentInput, EnrichmentResult


class Enricher(Protocol):
    @property
    def model(self) -> str: ...

    async def enrich(self, ctx: EnrichmentInput) -> EnrichmentResult: ...
