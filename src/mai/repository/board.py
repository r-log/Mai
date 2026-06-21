from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mai.db.models import BoardEvent, BoardItem


class BoardItemRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get(self, pcid: str) -> BoardItem | None:
        return await self._session.scalar(
            select(BoardItem).where(BoardItem.port_candidate_id == pcid))

    async def get_or_create(self, pcid: str, actor: str) -> BoardItem:
        existing = await self.get(pcid)
        if existing is not None:
            return existing
        item = BoardItem(port_candidate_id=pcid, status="open", updated_by=actor)
        self._session.add(item)
        return item

    async def active(self) -> list[BoardItem]:
        return list(await self._session.scalars(
            select(BoardItem).where(BoardItem.archived.is_(False))
            .order_by(BoardItem.port_candidate_id)))


class BoardEventRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def next_seq(self, pcid: str) -> int:
        current = await self._session.scalar(
            select(func.max(BoardEvent.seq))
            .where(BoardEvent.port_candidate_id == pcid))
        return (current or 0) + 1

    async def append(self, pcid: str, actor: str, action: str,
                     from_value: str | None, to_value: str | None) -> BoardEvent:
        event = BoardEvent(port_candidate_id=pcid, seq=await self.next_seq(pcid),
                           actor=actor, action=action,
                           from_value=from_value, to_value=to_value)
        self._session.add(event)
        return event

    async def for_item(self, pcid: str) -> list[BoardEvent]:
        return list(await self._session.scalars(
            select(BoardEvent).where(BoardEvent.port_candidate_id == pcid)
            .order_by(BoardEvent.seq)))
