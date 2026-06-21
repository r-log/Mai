import asyncio
import logging
from typing import Awaitable, Callable, Protocol

logger = logging.getLogger(__name__)


class Clock(Protocol):
    async def sleep(self, seconds: float) -> None: ...


class RealClock:
    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


async def run_cron(
    cycle: Callable[[], Awaitable[object]],
    *,
    interval_seconds: float,
    clock: Clock,
    max_runs: int | None = None,
) -> int:
    """Call cycle() forever (or max_runs times), sleeping between runs.

    A failing cycle is logged and swallowed so the backstop never dies.
    """
    runs = 0
    while max_runs is None or runs < max_runs:
        try:
            await cycle()
        except Exception:  # noqa: BLE001 - cron must survive a failed cycle
            logger.exception("refresh cycle failed; continuing")
        runs += 1
        if max_runs is not None and runs >= max_runs:
            break
        await clock.sleep(interval_seconds)
    return runs
