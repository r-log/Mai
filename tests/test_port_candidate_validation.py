"""Spec §12 validation gates for the port-debt signal (deterministic, in-DB).

Each test pins a correctness property the engine must never regress:
  - design-divergence (expansion) never becomes port-debt
  - a cherry-pick-propagated fix is recognized as present (no candidate)
  - a genuinely-missing shared fix surfaces as a high-confidence candidate
"""
from sqlalchemy import select

from mai.db.models import Commit, CommitFile, CommitPatch, PatchGroup, PortCandidate
from mai.repository.port_candidate import PortCandidateRepository
from mai.sync.classify import classify_subsystems
from mai.sync.portcandidates import compute_port_candidates
from mai.sync.propagate import compute_propagation


async def _commit(session, core, sha, patch_id, subsystem, message="m"):
    c = Commit(core=core, sha=sha, author="a", authored_at="t", committer="a",
               committed_at="t", message=message, parent_shas=["p"], is_merge=False)
    session.add(c)
    await session.flush()
    session.add(CommitPatch(commit_id=c.id, patch_id=patch_id))
    session.add(CommitFile(commit_id=c.id, path=f"{subsystem}/f.cpp", change_type="M",
                           added_lines=2, removed_lines=0, subsystem=subsystem))
    await session.flush()


async def _run(session):
    await compute_propagation(session)
    await classify_subsystems(session)
    return await compute_port_candidates(session)


async def test_gate_design_divergence_is_not_port_debt(session):
    # An expansion-content fix present in three, absent in two, must NOT be flagged.
    await _commit(session, "three", "s3", "PX", "src/game/Spells")
    await _commit(session, "two", "s2", "PZ", "src/game/Quests")
    await session.commit()
    result = await _run(session)
    assert result["candidates"] == 0
    assert await session.scalar(select(PortCandidate)) is None


async def test_gate_cherry_propagated_fix_is_not_port_debt(session):
    # three has the fix (P1, shared); two ported it as a different patch-id but cites
    # three's sha in a cherry trailer -> propagation marks two present -> no candidate.
    a = "a" * 40
    await _commit(session, "three", a, "P1", "src/shared/Log")
    await _commit(session, "two", "b" * 40, "P2", "src/shared/Log",
                  message=f"port\n\n(cherry picked from commit {a})")
    await session.commit()
    result = await _run(session)
    # P1 is present in both (two via cherry); P2 only in two but P2 is shared+absent in three
    # -> exactly the P2->three candidate, and NOT a spurious P1->two candidate.
    p1 = await session.scalar(select(PatchGroup).where(PatchGroup.patch_id == "P1"))
    assert await PortCandidateRepository(session).get(p1.id, "two") is None


async def test_gate_missing_shared_fix_surfaces_high_confidence(session):
    # The positive control: a shared fix in three, absent in two -> one high-confidence candidate.
    await _commit(session, "three", "s3", "P1", "src/shared/Database")
    await _commit(session, "two", "s2", "P9", "src/shared/Database")
    await session.commit()
    await _run(session)
    p1 = await session.scalar(select(PatchGroup).where(PatchGroup.patch_id == "P1"))
    cand = await PortCandidateRepository(session).get(p1.id, "two")
    assert cand is not None
    assert cand.confidence == "high" and cand.classification == "shared"
    assert cand.source_core == "three" and cand.subsystem == "src/shared/Database"
