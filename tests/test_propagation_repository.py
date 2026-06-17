from sqlalchemy import func, select

from mai.db.models import PatchGroup, Propagation
from mai.repository.propagation import PatchGroupRepository, PropagationRepository


async def test_get_or_create_is_idempotent(session):
    repo = PatchGroupRepository(session)
    a = await repo.get_or_create("pid-1")
    await session.flush()
    b = await repo.get_or_create("pid-1")
    assert a.id == b.id
    assert await session.scalar(select(func.count()).select_from(PatchGroup)) == 1


async def test_upsert_inserts_then_updates_single_row(session):
    pg = await PatchGroupRepository(session).get_or_create("pid-1")
    await session.flush()
    prop = PropagationRepository(session)
    await prop.upsert(pg.id, "three", present=False, via=None,
                      confidence="high", source_sha=None, evidence=[])
    await prop.upsert(pg.id, "three", present=True, via="patch_id",
                      confidence="high", source_sha="abc", evidence=["e1"])
    await session.commit()
    assert await session.scalar(select(func.count()).select_from(Propagation)) == 1
    row = await session.scalar(select(Propagation))
    assert row.present is True and row.via == "patch_id"
    assert row.source_sha == "abc" and row.evidence == ["e1"]
