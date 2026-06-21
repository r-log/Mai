class FakeClock:
    """Records requested sleeps instead of waiting."""

    def __init__(self) -> None:
        self.sleeps: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


class FakeDeployHook:
    """Counts deploy triggers."""

    def __init__(self) -> None:
        self.calls = 0

    async def trigger(self) -> None:
        self.calls += 1
