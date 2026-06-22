from mai.db.models import (Commit, CommitFile, CommitPatch, PatchGroup,
                           Propagation, SubsystemClass)
from mai.git.fake import FakeGitClient
from mai.repository.port_verdict import PortVerdictRepository
from mai.sync.verdicts import compute_verdicts


async def _fix(session, *, pg_id, source_core, source_sha, subsystem, classification,
               present_cores, absent_cores, added=4):
    """A patch_group present in present_cores, absent in absent_cores, whose source
    commit touches one file in `subsystem` (classified `classification`)."""
    session.add(PatchGroup(id=pg_id, patch_id=f"patch-{pg_id}"))
    session.add(SubsystemClass(subsystem=subsystem, classification=classification,
                               source="heuristic"))
    for c in present_cores:
        session.add(Propagation(patch_group_id=pg_id, core=c, present=True,
                                source_sha=source_sha if c == source_core else f"{c}-sha"))
    for c in absent_cores:
        session.add(Propagation(patch_group_id=pg_id, core=c, present=False, source_sha=None))
    commit = Commit(core=source_core, sha=source_sha, author="a", authored_at="t",
                    committer="a", committed_at="t", message="fix", parent_shas=["p"],
                    is_merge=False)
    session.add(commit)
    await session.flush()
    session.add(CommitFile(commit_id=commit.id, path=f"{subsystem}/x.cpp",
                           change_type="M", added_lines=added, removed_lines=0,
                           subsystem=subsystem))
    await session.flush()


async def test_shared_clean_apply_is_NEEDS(session):
    await _fix(session, pg_id="pg1", source_core="three", source_sha="s1",
               subsystem="src/shared/Database", classification="shared",
               present_cores=["three"], absent_cores=["two"])
    await session.commit()
    fake = FakeGitClient(diffs={("three", "s1"): "PATCH1"},
                         paths={"two": ["src/shared/Database/x.cpp"]})
    # default forward apply -> clean
    await compute_verdicts(session, fake)
    v = await PortVerdictRepository(session).get("pg1", "two")
    assert v.verdict == "needs" and v.apply_result == "clean" and v.relevance == "portable"


async def test_client_bound_clean_apply_is_REVIEW_not_NEEDS(session):
    # THE TRUTHFULNESS GATE: applies cleanly, but the area is client-bound -> never NEEDS
    await _fix(session, pg_id="pg2", source_core="three", source_sha="s2",
               subsystem="src/game/Server/Opcodes", classification="client_bound",
               present_cores=["three"], absent_cores=["two"])
    await session.commit()
    fake = FakeGitClient(diffs={("three", "s2"): "PATCH2"},
                         paths={"two": ["src/game/Server/Opcodes/x.cpp"]})  # exists + applies clean
    await compute_verdicts(session, fake)
    v = await PortVerdictRepository(session).get("pg2", "two")
    assert v.apply_result == "clean"        # git said it merges
    assert v.verdict == "review"            # ...but the relevance gate held it back
    assert v.relevance == "divergent"


async def test_file_absent_is_NOT_APPLICABLE(session):
    await _fix(session, pg_id="pg3", source_core="four", source_sha="s3",
               subsystem="src/game/MoPThing", classification="mixed",
               present_cores=["four"], absent_cores=["zero"])
    await session.commit()
    fake = FakeGitClient(diffs={("four", "s3"): "PATCH3"}, paths={"zero": []})  # nothing exists
    await compute_verdicts(session, fake)
    v = await PortVerdictRepository(session).get("pg3", "zero")
    assert v.verdict == "not_applicable" and v.apply_result == "file_absent"


async def test_reverse_applies_is_HAS_IT(session):
    await _fix(session, pg_id="pg4", source_core="three", source_sha="s4",
               subsystem="src/shared/Log", classification="shared",
               present_cores=["three"], absent_cores=["two"])
    await session.commit()
    fake = FakeGitClient(
        diffs={("three", "s4"): "PATCH4"},
        paths={"two": ["src/shared/Log/x.cpp"]},
        apply_results={("two", "PATCH4", True): "reverse_clean"})  # already present
    await compute_verdicts(session, fake)
    v = await PortVerdictRepository(session).get("pg4", "two")
    assert v.verdict == "has_it" and v.apply_result == "reverse_clean"


async def test_conflict_is_REVIEW(session):
    await _fix(session, pg_id="pg5", source_core="three", source_sha="s5",
               subsystem="src/shared/Auth", classification="shared",
               present_cores=["three"], absent_cores=["two"])
    await session.commit()
    fake = FakeGitClient(
        diffs={("three", "s5"): "PATCH5"},
        paths={"two": ["src/shared/Auth/x.cpp"]},
        apply_results={("two", "PATCH5", False): "conflict"})
    await compute_verdicts(session, fake)
    v = await PortVerdictRepository(session).get("pg5", "two")
    assert v.verdict == "review" and v.apply_result == "conflict"


async def test_incremental_cache_skips_unchanged(session):
    await _fix(session, pg_id="pg6", source_core="three", source_sha="s6",
               subsystem="src/shared/Db", classification="shared",
               present_cores=["three"], absent_cores=["two"])
    await session.commit()
    fake = FakeGitClient(diffs={("three", "s6"): "P6"},
                         paths={"two": ["src/shared/Db/x.cpp"]},
                         head_shas={"two": "headTWO"})
    first = await compute_verdicts(session, fake)
    second = await compute_verdicts(session, fake)   # nothing changed
    assert first["recomputed"] >= 1
    assert second["cached"] >= 1 and second["recomputed"] == 0
