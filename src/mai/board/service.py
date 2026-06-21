from sqlalchemy.ext.asyncio import AsyncSession

from mai.repository.board import BoardEventRepository, BoardItemRepository

ACTIONS = {"claim", "assign", "unassign", "status", "link_pr", "dismiss", "restore"}
VALID_STATUS = {"open", "claimed", "in_progress", "pr_linked", "dismissed"}


class ClaimConflict(Exception):
    """Raised when claiming a card already assigned to a different user."""


async def apply_action(session: AsyncSession, *, item_id: str, actor: str,
                       action: str, value: str | None = None,
                       reason: str | None = None,
                       related_pr: str | None = None):
    """Apply a board action and return the mutated item.

    The caller commits on success and must NOT commit the session if this raises
    (a pending BoardItem may be attached).
    """
    if action not in ACTIONS:
        raise ValueError(f"unknown action: {action}")

    items = BoardItemRepository(session)
    events = BoardEventRepository(session)
    item = await items.get_or_create(item_id, actor)

    if action == "claim":
        if item.assignee and item.assignee != actor:
            raise ClaimConflict(item.assignee)
        if item.assignee == actor:          # already claimed by same user — true no-op
            return item
        from_, item.assignee, item.status = item.assignee, actor, "claimed"
        await events.append(item_id, actor, "claim", from_, actor)

    elif action == "assign":
        if not value:
            raise ValueError("assign requires a target username")
        from_, item.assignee, item.status = item.assignee, value, "claimed"
        await events.append(item_id, actor, "assign", from_, value)

    elif action == "unassign":
        from_, item.assignee, item.status = item.assignee, None, "open"
        await events.append(item_id, actor, "unassign", from_, None)

    elif action == "status":
        if value not in VALID_STATUS:
            raise ValueError(f"invalid status: {value}")
        from_, item.status = item.status, value
        await events.append(item_id, actor, "status", from_, value)

    elif action == "link_pr":
        if not related_pr:
            raise ValueError("link_pr requires related_pr")
        from_, item.related_pr, item.status = item.related_pr, related_pr, "pr_linked"
        await events.append(item_id, actor, "link_pr", from_, related_pr)

    elif action == "dismiss":
        if not reason:
            raise ValueError("dismiss requires a reason")
        from_, item.status, item.dismiss_reason = item.status, "dismissed", reason
        await events.append(item_id, actor, "dismiss", from_, reason)

    elif action == "restore":
        from_, item.status, item.dismiss_reason = item.status, "open", None
        await events.append(item_id, actor, "restore", from_, "open")

    item.updated_by = actor
    return item
