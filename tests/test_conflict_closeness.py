import pytest

from mai.db.models import Commit, CommitFile, PatchGroup, Propagation, SubsystemClass
from mai.git.fake import FakeGitClient
from mai.repository.port_verdict import PortVerdictRepository
from mai.sync.verdicts import closeness_label, compute_verdicts


def test_closeness_label_thresholds():
    assert closeness_label(8, 10) == "near"      # 0.8
    assert closeness_label(4, 10) == "partial"   # 0.4
    assert closeness_label(3, 10) == "far"       # 0.3
    assert closeness_label(10, 10) == "near"


async def _fix(session, *, pg_id, subsystem, classification, source_sha):
    session.add(PatchGroup(id=pg_id, patch_id=f"p-{pg_id}"))
    session.add(SubsystemClass(subsystem=subsystem, classification=classification,
                               source="heuristic"))
    session.add(Propagation(patch_group_id=pg_id, core="three", present=True,
                            source_sha=source_sha))
    session.add(Propagation(patch_group_id=pg_id, core="two", present=False, source_sha=None))
    c = Commit(core="three", sha=source_sha, author="a", authored_at="t", committer="a",
               committed_at="t", message="fix", parent_shas=["p"], is_merge=False)
    session.add(c)
    await session.flush()
    session.add(CommitFile(commit_id=c.id, path=f"{subsystem}/x.cpp", change_type="M",
                           added_lines=2, removed_lines=0, subsystem=subsystem))
    await session.flush()


async def test_conflict_verdict_records_closeness(session):
    await _fix(session, pg_id="pgC", subsystem="src/shared/Db", classification="shared",
               source_sha="sC")
    await session.commit()
    fake = FakeGitClient(
        diffs={("three", "sC"): "PC"},
        paths={"two": ["src/shared/Db/x.cpp"]},
        apply_results={("two", "PC", False): "conflict"},
        fractions={("two", "PC"): (5, 6)})
    await compute_verdicts(session, fake)
    v = await PortVerdictRepository(session).get("pgC", "two")
    assert v.verdict == "review" and v.apply_result == "conflict"
    assert v.conflict_applied == 5 and v.conflict_total == 6
    assert any("5/6 hunks apply (near)" in e for e in v.evidence)


async def test_non_conflict_verdict_has_null_closeness(session):
    # a clean shared apply -> NEEDS, no fraction
    await _fix(session, pg_id="pgN", subsystem="src/shared/Db", classification="shared",
               source_sha="sN")
    await session.commit()
    fake = FakeGitClient(diffs={("three", "sN"): "PN"},
                         paths={"two": ["src/shared/Db/x.cpp"]})   # default forward -> clean
    await compute_verdicts(session, fake)
    v = await PortVerdictRepository(session).get("pgN", "two")
    assert v.verdict == "needs"
    assert v.conflict_applied is None and v.conflict_total is None
