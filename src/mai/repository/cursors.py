from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mai.db.models import SyncCursor


class CursorRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get(self, repo_full_name: str, source_type: str) -> str | None:
        return await self._session.scalar(
            select(SyncCursor.last_updated_at).where(
                SyncCursor.repo_full_name == repo_full_name,
                SyncCursor.source_type == source_type,
            )
        )

    async def set(self, repo_full_name: str, source_type: str, value: str) -> None:
        existing = await self._session.scalar(
            select(SyncCursor).where(
                SyncCursor.repo_full_name == repo_full_name,
                SyncCursor.source_type == source_type,
            )
        )
        if existing:
            existing.last_updated_at = value
        else:
            self._session.add(SyncCursor(
                repo_full_name=repo_full_name,
                source_type=source_type,
                last_updated_at=value,
            ))
