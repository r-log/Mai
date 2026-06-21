from mai.repository.board import BoardEventRepository, BoardItemRepository


async def test_get_or_create_is_idempotent(session):
    repo = BoardItemRepository(session)
    a = await repo.get_or_create("pg1:three", "antz")
    await session.commit()
    b = await repo.get_or_create("pg1:three", "madmax")
    await session.commit()
    assert a.port_candidate_id == b.port_candidate_id
    assert (await repo.get("pg1:three")).status == "open"


async def test_active_excludes_archived(session):
    repo = BoardItemRepository(session)
    keep = await repo.get_or_create("pg1:three", "antz")
    gone = await repo.get_or_create("pg2:two", "antz")
    gone.archived = True
    await session.commit()
    ids = [bi.port_candidate_id for bi in await repo.active()]
    assert "pg1:three" in ids
    assert "pg2:two" not in ids


async def test_event_seq_increments_per_item(session):
    events = BoardEventRepository(session)
    await events.append("pg1:three", "antz", "claim", None, "antz")
    await events.append("pg1:three", "antz", "status", "claimed", "in_progress")
    await events.append("pg2:two", "antz", "claim", None, "antz")
    await session.commit()
    seqs = [e.seq for e in await events.for_item("pg1:three")]
    assert seqs == [1, 2]
    assert [e.seq for e in await events.for_item("pg2:two")] == [1]
