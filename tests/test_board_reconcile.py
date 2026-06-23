from mai.board.service import apply_action
from mai.refresh.cycle import reconcile_board
from mai.repository.board import BoardItemRepository


async def test_reconcile_archives_items_without_open_candidate(session):
    # no PortCandidate rows exist → every active board item is now resolved
    await apply_action(session, item_id="gone:three", actor="antz", action="claim")
    await session.commit()
    archived = await reconcile_board(session)
    await session.commit()
    assert archived == 1
    item = await BoardItemRepository(session).get("gone:three")
    assert item.archived is True


async def test_reconcile_keeps_items_with_open_candidate(session, monkeypatch):
    import mai.refresh.cycle as cycle
    await apply_action(session, item_id="live:three", actor="antz", action="claim")
    await session.commit()

    async def fake_build(_session):
        return {"fixes": [{"needs": [{"item_id": "live:three"}], "review": []}],
                "summary": {}, "cores": []}

    monkeypatch.setattr(cycle, "build_port_verdicts", fake_build)
    archived = await reconcile_board(session)
    assert archived == 0
    assert (await BoardItemRepository(session).get("live:three")).archived is False
