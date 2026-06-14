from sqlalchemy import func, select

from mai.db.models import SyncCursor
from mai.repository.cursors import CursorRepository


async def test_cursor_get_returns_none_when_unset(session):
    assert await CursorRepository(session).get("mangoszero/server", "gh_issue") is None


async def test_cursor_set_then_get_roundtrips(session):
    cur = CursorRepository(session)
    await cur.set("mangoszero/server", "gh_issue", "2026-01-01T00:00:00Z")
    await session.commit()
    assert await cur.get("mangoszero/server", "gh_issue") == "2026-01-01T00:00:00Z"


async def test_cursor_set_updates_existing_single_row(session):
    cur = CursorRepository(session)
    await cur.set("mangoszero/server", "gh_issue", "2026-01-01T00:00:00Z")
    await cur.set("mangoszero/server", "gh_issue", "2026-02-01T00:00:00Z")
    await session.commit()
    assert await cur.get("mangoszero/server", "gh_issue") == "2026-02-01T00:00:00Z"
    assert await session.scalar(select(func.count()).select_from(SyncCursor)) == 1
