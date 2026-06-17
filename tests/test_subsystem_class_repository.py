from sqlalchemy import func, select

from mai.db.models import SubsystemClass
from mai.repository.subsystem_class import SubsystemClassRepository


async def test_upsert_auto_inserts_then_updates_single_row(session):
    repo = SubsystemClassRepository(session)
    assert await repo.upsert_auto("src/game/Object", "mixed") is True
    assert await repo.upsert_auto("src/game/Object", "shared") is True
    await session.commit()
    assert await session.scalar(select(func.count()).select_from(SubsystemClass)) == 1
    row = await repo.get("src/game/Object")
    assert row.classification == "shared" and row.source == "heuristic"


async def test_set_manual_then_auto_is_preserved(session):
    repo = SubsystemClassRepository(session)
    await repo.set_manual("src/game/Server", "shared")
    await session.commit()
    # a later auto-pass must NOT clobber the manual override
    assert await repo.upsert_auto("src/game/Server", "mixed") is False
    await session.commit()
    row = await repo.get("src/game/Server")
    assert row.classification == "shared" and row.source == "manual_override"


async def test_get_missing_returns_none(session):
    assert await SubsystemClassRepository(session).get("nope") is None
