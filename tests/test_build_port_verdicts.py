from mai.db.models import Commit, PatchGroup, PortVerdict, Repo
from mai.publish.dataviz import build_port_verdicts


async def test_export_groups_one_card_per_fix(session):
    session.add(Repo(full_name="mangosthree/server", core="three",
                     url="https://github.com/mangosthree/server"))
    session.add(PatchGroup(id="pg1", patch_id="pid1"))
    session.add(Commit(core="three", sha="abc1234567", author="a", authored_at="t",
                       committer="a", committed_at="t", message="Fix shared thing",
                       parent_shas=[], is_merge=False))
    # three has it (source), two needs, one review-conflict, four n/a
    session.add(PortVerdict(patch_group_id="pg1", core="two", verdict="needs",
                            apply_result="clean", relevance="portable",
                            source_core="three", source_sha="abc1234567",
                            subsystem="src/shared/Database", magnitude=10, tier="surgical"))
    session.add(PortVerdict(patch_group_id="pg1", core="one", verdict="review",
                            apply_result="conflict", relevance="divergent",
                            source_core="three", source_sha="abc1234567",
                            subsystem="src/shared/Database", magnitude=10, tier="surgical",
                            conflict_applied=4, conflict_total=5))
    session.add(PortVerdict(patch_group_id="pg1", core="four", verdict="not_applicable",
                            apply_result="file_absent", relevance="divergent",
                            source_core="three", source_sha="abc1234567",
                            subsystem="src/shared/Database", magnitude=10, tier="surgical"))
    await session.commit()

    out = await build_port_verdicts(session)

    assert out["summary"] == {"needs": 1, "review": 1, "na": 1, "has_it": 0, "fixes": 1}
    assert out["cores"] == ["zero", "one", "two", "three", "four"]
    card = out["fixes"][0]
    assert card["id"] == "pg1"
    assert card["title"] == "Fix shared thing"
    assert card["needs"] == [{"core": "two", "item_id": "pg1:two"}]
    assert card["review"][0]["item_id"] == "pg1:one"
    assert card["review"][0]["band"] == "near"      # 4/5 = 0.8
    assert card["review"][0]["applied"] == 4 and card["review"][0]["total"] == 5
    assert card["na"] == [{"core": "four", "reason": "code not present"}]


async def test_card_suppressed_when_no_needs_or_review(session):
    """A fix that is all has_it / na everywhere produces no card."""
    session.add(PatchGroup(id="pg2", patch_id="pid2"))
    session.add(Commit(core="three", sha="def456", author="a", authored_at="t",
                       committer="a", committed_at="t", message="Already everywhere",
                       parent_shas=[], is_merge=False))
    session.add(PortVerdict(patch_group_id="pg2", core="two", verdict="has_it",
                            apply_result="reverse_clean", relevance="portable",
                            source_core="three", source_sha="def456",
                            subsystem="src/shared/Log", magnitude=5, tier="surgical"))
    session.add(PortVerdict(patch_group_id="pg2", core="one", verdict="not_applicable",
                            apply_result="file_absent", relevance="divergent",
                            source_core="three", source_sha="def456",
                            subsystem="src/shared/Log", magnitude=5, tier="surgical"))
    await session.commit()

    out = await build_port_verdicts(session)

    assert out["summary"]["fixes"] == 0
    assert out["fixes"] == []


async def test_review_sorted_near_before_far(session):
    """REVIEW entries are sorted near -> partial -> far within a card."""
    session.add(PatchGroup(id="pg3", patch_id="pid3"))
    session.add(Commit(core="three", sha="ghi789", author="a", authored_at="t",
                       committer="a", committed_at="t", message="Multi-review fix",
                       parent_shas=[], is_merge=False))
    # far conflict: 1/10 = 0.1
    session.add(PortVerdict(patch_group_id="pg3", core="zero", verdict="review",
                            apply_result="conflict", relevance="divergent",
                            source_core="three", source_sha="ghi789",
                            subsystem="src/shared/Auth", magnitude=8, tier="surgical",
                            conflict_applied=1, conflict_total=10))
    # near conflict: 9/10 = 0.9
    session.add(PortVerdict(patch_group_id="pg3", core="one", verdict="review",
                            apply_result="conflict", relevance="divergent",
                            source_core="three", source_sha="ghi789",
                            subsystem="src/shared/Auth", magnitude=8, tier="surgical",
                            conflict_applied=9, conflict_total=10))
    await session.commit()

    out = await build_port_verdicts(session)

    assert out["summary"]["fixes"] == 1
    card = out["fixes"][0]
    assert len(card["review"]) == 2
    # near should come first (one), then far (zero)
    assert card["review"][0]["core"] == "one"
    assert card["review"][0]["band"] == "near"
    assert card["review"][1]["core"] == "zero"
    assert card["review"][1]["band"] == "far"


async def test_summary_counts_across_multiple_fixes(session):
    """Summary aggregates needs/review/na/has_it across all actionable fix cards."""
    for i, (pg_id, patch_id) in enumerate([("pgA", "pidA"), ("pgB", "pidB")]):
        session.add(PatchGroup(id=pg_id, patch_id=patch_id))
        session.add(Commit(core="three", sha=f"sha{i}", author="a", authored_at="t",
                           committer="a", committed_at="t", message=f"Fix {i}",
                           parent_shas=[], is_merge=False))
        session.add(PortVerdict(patch_group_id=pg_id, core="two", verdict="needs",
                                apply_result="clean", relevance="portable",
                                source_core="three", source_sha=f"sha{i}",
                                subsystem="src/shared/X", magnitude=3, tier="surgical"))
        session.add(PortVerdict(patch_group_id=pg_id, core="one", verdict="has_it",
                                apply_result="reverse_clean", relevance="portable",
                                source_core="three", source_sha=f"sha{i}",
                                subsystem="src/shared/X", magnitude=3, tier="surgical"))
    await session.commit()

    out = await build_port_verdicts(session)

    assert out["summary"]["fixes"] == 2
    assert out["summary"]["needs"] == 2
    assert out["summary"]["has_it"] == 2
    assert out["summary"]["review"] == 0
    assert out["summary"]["na"] == 0


async def test_source_url_built_from_repo(session):
    """source_url is constructed from the Repo row for source_core."""
    session.add(Repo(full_name="mangosthree/server", core="three",
                     url="https://github.com/mangosthree/server"))
    session.add(PatchGroup(id="pg4", patch_id="pid4"))
    session.add(Commit(core="three", sha="abc0000001", author="a", authored_at="t",
                       committer="a", committed_at="t", message="URL test fix",
                       parent_shas=[], is_merge=False))
    session.add(PortVerdict(patch_group_id="pg4", core="two", verdict="needs",
                            apply_result="clean", relevance="portable",
                            source_core="three", source_sha="abc0000001",
                            subsystem="src/shared/X", magnitude=3, tier="surgical"))
    await session.commit()

    out = await build_port_verdicts(session)

    card = out["fixes"][0]
    assert card["source_url"] == "https://github.com/mangosthree/server/commit/abc0000001"


async def test_title_falls_back_to_subsystem_when_no_commit(session):
    """If no Commit row matches source_core+source_sha, title falls back gracefully."""
    session.add(PatchGroup(id="pg5", patch_id="pid5"))
    # no Commit added — source_sha is unknown
    session.add(PortVerdict(patch_group_id="pg5", core="two", verdict="needs",
                            apply_result="clean", relevance="portable",
                            source_core="three", source_sha="deadbeef",
                            subsystem="src/shared/DB", magnitude=5, tier="surgical"))
    await session.commit()

    out = await build_port_verdicts(session)

    card = out["fixes"][0]
    assert "src/shared/DB" in card["title"] or "deadbeef"[:8] in card["title"]
