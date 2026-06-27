# src/mai/judge/fake.py
from mai.judge.schema import ReviewOpinion


class FakeJudge:
    """Deterministic ReviewJudge for tests. Records call count + last model."""

    def __init__(self, opinion: ReviewOpinion | None = None,
                 raises: Exception | None = None):
        self._opinion = opinion or ReviewOpinion(
            assessment="portable", confidence=0.8, reason="ok")
        self._raises = raises
        self.calls = 0
        self.last_model: str | None = None

    async def judge(self, evidence: dict, model: str) -> ReviewOpinion:
        self.calls += 1
        self.last_model = model
        if self._raises is not None:
            raise self._raises
        return self._opinion
