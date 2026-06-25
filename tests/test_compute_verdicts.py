from pathlib import Path

from mai.db.models import (Commit, CommitFile, CommitPatch, PatchGroup,
                           Propagation, SubsystemClass)
from mai.git.fake import FakeGitClient
from mai.portability.types import GATE_SUITE_VERSION
from mai.repository.port_verdict import PortVerdictRepository
from mai.sync.verdicts import compute_verdicts

_FIX = Path(__file__).parent / "fixtures"


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


async def test_two_fixes_do_not_cross_contaminate(session):
    # pgA: shared subsystem -> should be NEEDS (portable + clean apply)
    await _fix(session, pg_id="pgA", source_core="three", source_sha="sA",
               subsystem="src/shared/X", classification="shared",
               present_cores=["three"], absent_cores=["two"])
    # pgB: client_bound subsystem -> should be REVIEW (divergent even with clean apply)
    await _fix(session, pg_id="pgB", source_core="three", source_sha="sB",
               subsystem="src/game/Server/Opcodes", classification="client_bound",
               present_cores=["three"], absent_cores=["two"])
    await session.commit()
    fake = FakeGitClient(
        diffs={("three", "sA"): "PA", ("three", "sB"): "PB"},
        paths={"two": ["src/shared/X/x.cpp", "src/game/Server/Opcodes/x.cpp"]})
    await compute_verdicts(session, fake)
    repo = PortVerdictRepository(session)
    assert (await repo.get("pgA", "two")).verdict == "needs"
    assert (await repo.get("pgB", "two")).verdict == "review"
    # each fix must have exactly one verdict — no contamination from the other's propagation
    assert len(await repo.for_fix("pgA")) == 1
    assert len(await repo.for_fix("pgB")) == 1


async def test_state_column_populated_additively(session):
    await _fix(session, pg_id="pgS", source_core="three", source_sha="sS",
               subsystem="src/shared/Db", classification="shared",
               present_cores=["three"], absent_cores=["two"])
    await session.commit()
    fake = FakeGitClient(diffs={("three", "sS"): "PATCH"},
                         paths={"two": ["src/shared/Db/x.cpp"]})
    await compute_verdicts(session, fake)
    v = await PortVerdictRepository(session).get("pgS", "two")
    assert v.verdict == "needs"                 # legacy field unchanged
    assert v.state == "portable"                # additive classifier state
    assert v.gate_version == GATE_SUITE_VERSION


async def test_229_state_diverges_from_legacy_needs(session):
    """The whole point: legacy `verdict` stays 'needs' (false positive), but the
    additive `state` is NOT_APPLICABLE because `loc` is absent in the target. The feed
    (which reads `verdict`) is untouched; the truth is recorded in `state`."""
    pr229 = (_FIX / "pr229.patch").read_text(encoding="utf-8")
    three = (_FIX / "dbcfileloader_three_229.cpp").read_text(encoding="utf-8")
    zero = (_FIX / "dbcfileloader_zero.cpp").read_text(encoding="utf-8")
    path = "src/shared/DataStores/DBCFileLoader.cpp"

    session.add(PatchGroup(id="pg229", patch_id="patch-229"))
    session.add(SubsystemClass(subsystem="src/shared/DataStores",
                               classification="shared", source="heuristic"))
    session.add(Propagation(patch_group_id="pg229", core="three", present=True,
                            source_sha="229"))
    session.add(Propagation(patch_group_id="pg229", core="zero", present=False,
                            source_sha=None))
    commit = Commit(core="three", sha="229", author="a", authored_at="t",
                    committer="a", committed_at="t", message="DBC fix",
                    parent_shas=["p"], is_merge=False)
    session.add(commit)
    await session.flush()
    session.add(CommitFile(commit_id=commit.id, path=path, change_type="M",
                           added_lines=6, removed_lines=2,
                           subsystem="src/shared/DataStores"))
    await session.commit()

    fake = FakeGitClient(
        diffs={("three", "229"): pr229},
        paths={"zero": [path]},                       # file exists -> not file_absent
        head_shas={"zero": "H"},
        files={("three", "229", path): three, ("zero", "H", path): zero})
    # default forward apply -> "clean": the textual trap that fools the legacy gate
    await compute_verdicts(session, fake)

    v = await PortVerdictRepository(session).get("pg229", "zero")
    assert v.verdict == "needs"              # legacy false positive (clean + shared)
    assert v.apply_result == "clean"
    assert v.state == "not_applicable"       # classifier catches it
    assert any("loc" in d for d in v.state_evidence)


async def test_one_failing_core_does_not_abort_batch(session):
    # a git error on one (fix, core) is recorded and skipped, not fatal
    await _fix(session, pg_id="pgE", source_core="three", source_sha="sE",
               subsystem="src/shared/Db", classification="shared",
               present_cores=["three"], absent_cores=["two"])
    await session.commit()

    class Boom(FakeGitClient):
        async def apply_check(self, core, patch_text, *, reverse=False):
            raise RuntimeError("git blew up")

    fake = Boom(diffs={("three", "sE"): "PE"},
                paths={"two": ["src/shared/Db/x.cpp"]})
    counts = await compute_verdicts(session, fake)
    assert counts["errors"] >= 1
    assert await PortVerdictRepository(session).get("pgE", "two") is None
