from mai.repository.port_verdict import PortVerdictRepository


async def test_upsert_is_idempotent_and_overwrites(session):
    repo = PortVerdictRepository(session)
    await repo.upsert("pg1", "two", verdict="needs", apply_result="clean",
                      relevance="portable", source_core="three", source_sha="s1",
                      base_sha="b1", subsystem="src/shared", magnitude=4, tier="surgical",
                      confidence="high", similar_commit=None, evidence=["e"])
    await session.commit()
    # re-upsert same key with a new verdict -> overwrites (derived, no status to preserve)
    await repo.upsert("pg1", "two", verdict="review", apply_result="conflict",
                      relevance="portable", source_core="three", source_sha="s2",
                      base_sha="b2", subsystem="src/shared", magnitude=4, tier="surgical",
                      confidence="medium", similar_commit=None, evidence=["e2"])
    await session.commit()
    v = await repo.get("pg1", "two")
    assert v.verdict == "review" and v.source_sha == "s2" and v.base_sha == "b2"


async def test_actionable_and_for_fix(session):
    repo = PortVerdictRepository(session)
    for core, verdict in [("two", "needs"), ("one", "review"),
                          ("zero", "not_applicable"), ("four", "has_it")]:
        await repo.upsert("pg1", core, verdict=verdict, apply_result="clean",
                          relevance="portable", source_core="three", source_sha="s1",
                          base_sha="b1", subsystem="src/shared", magnitude=1, tier="surgical",
                          confidence="high", similar_commit=None, evidence=[])
    await session.commit()
    actionable = {(v.patch_group_id, v.core) for v in await repo.actionable()}
    assert actionable == {("pg1", "two"), ("pg1", "one")}     # needs + review only
    assert len(await repo.for_fix("pg1")) == 4
