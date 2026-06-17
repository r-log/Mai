from sqlalchemy import func, select

from mai.db.models import CommitPatch, PatchGroup, Propagation
from mai.db.models import Commit
from mai.sync.propagate import compute_propagation


async def _add(session, core, sha, patch_id, message="m"):
    c = Commit(core=core, sha=sha, author="a", authored_at="t", committer="a",
               committed_at="t", message=message, parent_shas=["p"], is_merge=False)
    session.add(c)
    await session.flush()
    session.add(CommitPatch(commit_id=c.id, patch_id=patch_id))
    await session.flush()
    return c


async def _prop(session, patch_id, core):
    return await session.scalar(
        select(Propagation).join(PatchGroup,
                                 PatchGroup.id == Propagation.patch_group_id)
        .where(PatchGroup.patch_id == patch_id, Propagation.core == core)
    )


async def test_present_and_absent_matrix(session):
    await _add(session, "three", "s_three", "P1")
    await _add(session, "two", "s_two", "P1")
    await _add(session, "zero", "s_zero", "P9")
    await session.commit()

    result = await compute_propagation(session)
    assert result["groups"] == 2          # P1, P9
    # P1 present in three+two, absent in zero
    assert (await _prop(session, "P1", "three")).present is True
    assert (await _prop(session, "P1", "two")).present is True
    assert (await _prop(session, "P1", "zero")).present is False
    # P9 present only in zero
    assert (await _prop(session, "P9", "zero")).present is True
    assert (await _prop(session, "P9", "three")).present is False
    # 2 groups x 3 cores = 6 rows; 3 present (P1:two,three; P9:zero), 3 absent
    assert result["present"] == 3 and result["absent"] == 3


async def test_cherry_trailer_links_despite_patch_mismatch(session):
    a = "a" * 40
    await _add(session, "three", a, "P1")
    # two has a DIFFERENT patch-id but cites three's commit as the cherry source
    await _add(session, "two", "b" * 40, "P2",
               message=f"port fix\n\n(cherry picked from commit {a})")
    await session.commit()

    result = await compute_propagation(session)
    p1_two = await _prop(session, "P1", "two")
    assert p1_two.present is True
    assert "cherry_trailer" in p1_two.via
    assert result["cherry_links"] == 1


async def test_recompute_is_idempotent(session):
    await _add(session, "three", "s1", "P1")
    await _add(session, "two", "s2", "P1")
    await session.commit()
    await compute_propagation(session)
    await compute_propagation(session)
    assert await session.scalar(select(func.count()).select_from(PatchGroup)) == 1
    assert await session.scalar(select(func.count()).select_from(Propagation)) == 2
