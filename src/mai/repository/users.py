from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mai.db.models import User


class UserRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get(self, username: str) -> User | None:
        return await self._session.scalar(
            select(User).where(User.username == username))

    async def create(self, username: str, password_hash: str, *,
                     is_maintainer: bool = False, display_name: str = "") -> User:
        user = User(username=username, password_hash=password_hash,
                    is_maintainer=is_maintainer,
                    display_name=display_name or username,
                    must_change_password=True)
        self._session.add(user)
        return user

    async def set_password(self, user: User, password_hash: str) -> None:
        user.password_hash = password_hash
        user.must_change_password = False

    async def all(self) -> list[User]:
        return list(await self._session.scalars(
            select(User).order_by(User.username)))
