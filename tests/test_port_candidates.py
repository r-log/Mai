from sqlalchemy import func, select

from mai.db.models import Commit, CommitFile, CommitPatch, PortCandidate
from mai.repository.port_candidate import PortCandidateRepository
from mai.sync.classify import classify_subsystems
from mai.sync.portcandidates import compute_port_candidates
from mai.sync.propagate import compute_propagation


async def _commit(session, core, sha, patch_id, subsystem, added=3, removed=1):
    c = Commit(core=core, sha=sha, author="a", authored_at="t", committer="a",
               committed_at="t", message="m", parent_shas=["p"], is_merge=False)
    session.add(c)
    await session.flush()
    session.add(CommitPatch(commit_id=c.id, patch_id=patch_id))
    session.add(CommitFile(commit_id=c.id, path=f"{subsystem}/f.cpp", change_type="M",
                           added_lines=added, removed_lines=removed, subsystem=subsystem))
    await session.flush()


async def _analyze(session):
    await compute_propagation(session)
    await classify_subsystems(session)
    return await compute_port_candidates(session)


async def test_shared_absent_emits_high_confidence_candidate(session):
    # fix present in three (shared subsystem), absent in two
    await _commit(session, "three", "s_three", "P1", "src/shared/Log", added=4, removed=2)
    await _commit(session, "two", "s_two", "P9", "src/shared/Log")  # gives 'two' a presence in the universe
    await session.commit()

    result = await _analyze(session)
    assert result["candidates"] == 2  # P1→two and P9→three are both shared-absent
    from mai.db.models import PatchGroup
    p1 = await session.scalar(select(PatchGroup).where(PatchGroup.patch_id == "P1"))
    c = await PortCandidateRepository(session).get(p1.id, "two")
    assert c is not None
    assert c.source_core == "three" and c.target_core == "two"
    assert c.classification == "shared" and c.confidence == "high"
    assert c.magnitude == 6  # 4 + 2
    assert c.status == "open" and c.subsystem == "src/shared/Log"
    assert any("three" in e for e in c.evidence)


async def test_expansion_only_fix_emits_no_candidate(session):
    # a fix touching ONLY an expansion subsystem, present in three, absent in two
    await _commit(session, "three", "s_three", "PX", "src/game/Spells")
    await _commit(session, "two", "s_two", "PY", "src/game/Spells")
    await session.commit()
    result = await _analyze(session)
    assert result["candidates"] == 0
    assert result["skipped_unportable"] == 2  # both groups skipped (expansion)
    assert await session.scalar(select(func.count()).select_from(PortCandidate)) == 0


async def test_fully_propagated_fix_emits_no_candidate(session):
    # same patch-id in BOTH forks -> present everywhere -> nothing to port
    await _commit(session, "three", "s_three", "P1", "src/shared/Log")
    await _commit(session, "two", "s_two", "P1", "src/shared/Log")
    await session.commit()
    result = await _analyze(session)
    assert result["candidates"] == 0


async def test_recompute_idempotent_and_auto_resolves_when_ported(session):
    await _commit(session, "three", "s_three", "P1", "src/shared/Log")
    await _commit(session, "two", "s_two", "P9", "src/shared/Log")
    await session.commit()
    await _analyze(session)
    from mai.db.models import PatchGroup
    p1 = await session.scalar(select(PatchGroup).where(PatchGroup.patch_id == "P1"))
    first = await PortCandidateRepository(session).get(p1.id, "two")
    assert first.status == "open"

    # 'two' now acquires P1 (someone ported it) -> recompute should auto-resolve the candidate
    await _commit(session, "two", "s_two_port", "P1", "src/shared/Log")
    await session.commit()
    result = await _analyze(session)
    resolved = await PortCandidateRepository(session).get(p1.id, "two")
    assert resolved.status == "ported"
    assert result["auto_resolved"] == 1
    # idempotent: no duplicate rows
    assert await session.scalar(
        select(func.count()).select_from(PortCandidate).where(
            PortCandidate.patch_group_id == p1.id, PortCandidate.target_core == "two")) == 1


async def test_vendored_fix_emits_no_candidate(session):
    # a fix touching only a vendored (dep/) subsystem must NOT graduate
    await _commit(session, "three", "s3", "PV", "dep/zlib")
    await _commit(session, "two", "s2", "PW", "dep/zlib")
    await session.commit()
    result = await _analyze(session)
    assert result["candidates"] == 0
    assert result["skipped_unportable"] == 2


async def test_tier_distribution_reported(session):
    # one surgical (mag 2) shared fix present in three, absent in two
    await _commit(session, "three", "s3", "P1", "src/shared/Log", added=1, removed=1)
    await _commit(session, "two", "s2", "P9", "src/shared/Log", added=1, removed=1)
    await session.commit()
    result = await _analyze(session)
    assert "tiers" in result
    assert result["tiers"]["surgical"] == 2   # both P1->two and P9->three are tiny
    assert result["tiers"]["bulk"] == 0
