from mai.db.models import (Commit, PatchGroup, PortCandidate, Repo)
from mai.publish.dataviz import build_port_candidates


async def _pg(session, patch_id):
    pg = PatchGroup(patch_id=patch_id)
    session.add(pg)
    await session.flush()
    return pg


async def _commit(session, core, sha, message):
    c = Commit(core=core, sha=sha, author="a", authored_at="t", committer="a",
               committed_at="t", message=message, parent_shas=["p"], is_merge=False)
    session.add(c)
    await session.flush()


async def test_groups_by_target_sorts_and_joins(session):
    session.add(Repo(full_name="r-log/server-two", core="two",
                     url="https://github.com/r-log/server-two"))
    await _commit(session, "two", "ABC123", "Fix realm auth length\n\nbody")
    pg1 = await _pg(session, "patchidA1")
    pg2 = await _pg(session, "patchidB2")
    # a small (mag 200) and a surgical (mag 4) candidate, both target three from two
    session.add(PortCandidate(patch_group_id=pg1.id, source_core="two", target_core="three",
                              subsystem="src/shared/Auth", classification="shared", magnitude=200,
                              tier="small", confidence="high", evidence=["e1"], status="open",
                              source_sha="ABC123"))
    session.add(PortCandidate(patch_group_id=pg2.id, source_core="two", target_core="three",
                              subsystem="src/shared/Auth", classification="shared", magnitude=4,
                              tier="surgical", confidence="high", evidence=["e2"], status="open",
                              source_sha="ABC123"))
    # an excluded (dismissed) candidate
    session.add(PortCandidate(patch_group_id=pg1.id, source_core="two", target_core="one",
                              subsystem="src/shared/Auth", classification="shared", magnitude=4,
                              tier="surgical", confidence="high", evidence=[], status="dismissed",
                              source_sha="ABC123"))
    await session.commit()

    out = await build_port_candidates(session)
    assert out["summary"]["total"] == 2          # only open
    assert out["summary"]["tiers"] == {"surgical": 1, "small": 1, "moderate": 0, "bulk": 0}
    cols = {c["core"]: c for c in out["columns"]}
    assert [c["core"] for c in out["columns"]] == ["zero", "one", "two", "three", "four"]  # all cores, ordered
    three = cols["three"]
    assert three["count"] == 2
    assert [x["tier"] for x in three["candidates"]] == ["surgical", "small"]  # surgical first
    card = three["candidates"][0]
    assert card["id"] == f"{pg2.id}:three"
    assert card["title"] == "Fix realm auth length"
    assert card["source_core"] == "two"
    assert card["source_url"] == "https://github.com/r-log/server-two/commit/ABC123"
    assert card["patch_id"] == "patchidB2"
    assert cols["zero"]["count"] == 0            # empty fork still gets a column


async def test_title_and_url_fallbacks(session):
    pg = await _pg(session, "p")
    session.add(PortCandidate(patch_group_id=pg.id, source_core="two", target_core="three",
                              subsystem="src/shared/Log", classification="shared", magnitude=2,
                              tier="surgical", confidence="high", evidence=[], status="open",
                              source_sha="NOSHA"))  # no Commit, no Repo
    await session.commit()
    out = await build_port_candidates(session)
    card = {c["core"]: c for c in out["columns"]}["three"]["candidates"][0]
    assert card["title"] == "src/shared/Log fix (NOSHA)"  # fallback title
    assert card["source_url"] is None                      # no repo -> no link
