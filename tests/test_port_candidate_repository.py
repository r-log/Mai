from sqlalchemy import func, select

from mai.db.models import PatchGroup, PortCandidate
from mai.repository.port_candidate import PortCandidateRepository


async def _pg(session, patch_id="P1") -> str:
    pg = PatchGroup(patch_id=patch_id)
    session.add(pg)
    await session.flush()
    return pg.id


async def test_upsert_inserts_open_then_updates_preserving_status(session):
    pg_id = await _pg(session)
    repo = PortCandidateRepository(session)
    await repo.upsert(pg_id, "two", source_core="three", subsystem="src/shared/Log",
                      classification="shared", magnitude=5, confidence="high",
                      evidence=["e1"], source_sha="abc")
    await session.commit()
    row = await repo.get(pg_id, "two")
    assert row.status == "open" and row.magnitude == 5

    # a human dismisses it
    await repo.mark_status(row, "dismissed")
    await session.commit()
    # recompute upserts again with new magnitude — status must survive
    await repo.upsert(pg_id, "two", source_core="three", subsystem="src/shared/Log",
                      classification="shared", magnitude=9, confidence="high",
                      evidence=["e2"], source_sha="abc")
    await session.commit()
    row = await repo.get(pg_id, "two")
    assert await session.scalar(select(func.count()).select_from(PortCandidate)) == 1
    assert row.status == "dismissed"     # preserved
    assert row.magnitude == 9            # recomputed fields updated


async def test_open_candidates_filters_by_status(session):
    pg_id = await _pg(session)
    repo = PortCandidateRepository(session)
    await repo.upsert(pg_id, "two", source_core="three", subsystem="s",
                      classification="shared", magnitude=1, confidence="high",
                      evidence=[], source_sha="a")
    await repo.upsert(pg_id, "one", source_core="three", subsystem="s",
                      classification="shared", magnitude=1, confidence="high",
                      evidence=[], source_sha="a")
    await session.commit()
    row = await repo.get(pg_id, "one")
    await repo.mark_status(row, "ported")
    await session.commit()
    opens = await repo.open_candidates()
    assert [c.target_core for c in opens] == ["two"]
