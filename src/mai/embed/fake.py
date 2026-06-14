import hashlib


class FakeEmbedder:
    """Deterministic Embedder for tests: same text -> same vector. Counts calls."""

    def __init__(self, dimensions: int = 8, model: str = "fake-embed"):
        self._dim = dimensions
        self._model = model
        self.calls = 0

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dim

    async def embed(self, text: str) -> list[float]:
        self.calls += 1
        digest = hashlib.sha256(text.encode()).digest()
        return [digest[i % len(digest)] / 255.0 for i in range(self._dim)]
