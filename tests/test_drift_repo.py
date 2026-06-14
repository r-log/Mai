from sqlalchemy import func, select

from mai.db.models import DriftObservation
from mai.repository.drift import DriftRepository

STATS = {"shared": 10, "diverged": 4, "identical": 6, "only_a": 1, "only_b": 2}


async def test_upsert_inserts_then_updates_single_row(session):
    repo = DriftRepository(session)
    await repo.upsert("zero/server", "two/server", "src/game/Object", STATS)
    await session.commit()
    await repo.upsert("zero/server", "two/server", "src/game/Object",
                      {**STATS, "diverged": 5})  # same key -> update, no dup
    await session.commit()
    rows = await repo.for_pair("zero/server", "two/server")
    assert len(rows) == 1
    assert rows[0].diverged == 5
    assert rows[0].shared == 10
    assert await session.scalar(select(func.count()).select_from(DriftObservation)) == 1


async def test_for_pair_returns_only_that_pair(session):
    repo = DriftRepository(session)
    await repo.upsert("zero/server", "two/server", "src/shared", STATS)
    await repo.upsert("one/server", "two/server", "src/shared", STATS)
    await session.commit()
    assert len(await repo.for_pair("zero/server", "two/server")) == 1
