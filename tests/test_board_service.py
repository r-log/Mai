import pytest

from mai.board.service import ClaimConflict, apply_action
from mai.repository.board import BoardEventRepository, BoardItemRepository


async def test_claim_sets_assignee_and_status_and_audits(session):
    item = await apply_action(session, item_id="pg1:three", actor="antz", action="claim")
    await session.commit()
    assert item.assignee == "antz"
    assert item.status == "claimed"
    events = await BoardEventRepository(session).for_item("pg1:three")
    assert [(e.action, e.to_value) for e in events] == [("claim", "antz")]


async def test_claim_conflict_when_someone_else_holds_it(session):
    await apply_action(session, item_id="pg1:three", actor="antz", action="claim")
    await session.commit()
    with pytest.raises(ClaimConflict):
        await apply_action(session, item_id="pg1:three", actor="madmax", action="claim")


async def test_reclaim_by_same_user_is_noop_ok(session):
    await apply_action(session, item_id="pg1:three", actor="antz", action="claim")
    await session.commit()
    item = await apply_action(session, item_id="pg1:three", actor="antz", action="claim")
    await session.commit()
    assert item.assignee == "antz"


async def test_assign_sets_other_user(session):
    item = await apply_action(session, item_id="pg1:three", actor="r-log",
                              action="assign", value="madmax")
    await session.commit()
    assert item.assignee == "madmax"
    assert item.status == "claimed"


async def test_unassign_clears(session):
    await apply_action(session, item_id="pg1:three", actor="antz", action="claim")
    item = await apply_action(session, item_id="pg1:three", actor="antz", action="unassign")
    await session.commit()
    assert item.assignee is None
    assert item.status == "open"


async def test_status_transition_validates(session):
    item = await apply_action(session, item_id="pg1:three", actor="antz",
                              action="status", value="in_progress")
    await session.commit()
    assert item.status == "in_progress"
    with pytest.raises(ValueError):
        await apply_action(session, item_id="pg1:three", actor="antz",
                           action="status", value="bogus")


async def test_link_pr_sets_status_pr_linked(session):
    item = await apply_action(session, item_id="pg1:three", actor="antz",
                              action="link_pr", related_pr="https://github.com/x/y/pull/1")
    await session.commit()
    assert item.related_pr.endswith("/pull/1")
    assert item.status == "pr_linked"


async def test_dismiss_requires_reason_then_restore(session):
    with pytest.raises(ValueError):
        await apply_action(session, item_id="pg1:three", actor="r-log", action="dismiss")
    item = await apply_action(session, item_id="pg1:three", actor="r-log",
                              action="dismiss", reason="WotLK-only, N/A")
    await session.commit()
    assert item.status == "dismissed"
    assert item.dismiss_reason == "WotLK-only, N/A"
    restored = await apply_action(session, item_id="pg1:three", actor="r-log",
                                  action="restore")
    await session.commit()
    assert restored.status == "open"
    assert restored.dismiss_reason is None


async def test_unknown_action_raises(session):
    with pytest.raises(ValueError):
        await apply_action(session, item_id="pg1:three", actor="antz", action="frobnicate")
