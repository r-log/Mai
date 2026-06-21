from mai.refresh.fake import FakeClock
from mai.refresh.trigger import run_cron


async def test_run_cron_runs_cycle_n_times():
    calls = []

    async def cycle():
        calls.append(1)

    clock = FakeClock()
    runs = await run_cron(cycle, interval_seconds=5, clock=clock, max_runs=3)
    assert runs == 3
    assert len(calls) == 3
    assert clock.sleeps == [5, 5]  # sleeps between runs, none after the last


async def test_run_cron_survives_a_failing_cycle():
    calls = []

    async def cycle():
        calls.append(1)
        raise RuntimeError("boom")

    clock = FakeClock()
    runs = await run_cron(cycle, interval_seconds=1, clock=clock, max_runs=2)
    assert runs == 2
    assert len(calls) == 2
