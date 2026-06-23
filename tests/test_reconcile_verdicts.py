from mai.db.models import Commit, PatchGroup, PortVerdict
from mai.refresh.cycle import reconcile_board
from mai.repository.board import BoardItemRepository


async def test_reconcile_archives_orphan_keeps_active(session):
    """Archive BoardItems whose port_candidate_id is not in the actionable verdict set.

    pg1:two is actionable (verdict=needs) → must stay (archived=False).
    pgX:zero has no PortVerdict at all → must be archived (archived=True).
    Return value must equal the count archived (1).
    """
    # Seed a PatchGroup + Commit so build_port_verdicts can build the card.
    session.add(PatchGroup(id="pg1", patch_id="pid1"))
    session.add(Commit(core="three", sha="abc1234567", author="a", authored_at="t",
                       committer="a", committed_at="t", message="Fix shared thing",
                       parent_shas=[], is_merge=False))
    # pg1 -> two: actionable (needs)
    session.add(PortVerdict(patch_group_id="pg1", core="two", verdict="needs",
                            apply_result="clean", relevance="portable",
                            source_core="three", source_sha="abc1234567",
                            subsystem="src/shared/Database", magnitude=10, tier="surgical"))
    await session.commit()

    repo = BoardItemRepository(session)
    # Active item that matches the actionable verdict → should be KEPT.
    kept = await repo.get_or_create("pg1:two", "system")
    # Active item with no verdict at all → should be ARCHIVED.
    orphan = await repo.get_or_create("pgX:zero", "system")
    await session.commit()

    count = await reconcile_board(session)

    assert count == 1
    assert orphan.archived is True
    assert kept.archived is False
