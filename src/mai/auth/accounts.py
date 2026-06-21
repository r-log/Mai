import secrets

from sqlalchemy.ext.asyncio import AsyncSession

from mai.auth.hasher import PasswordHasher
from mai.repository.users import UserRepository


def generate_password() -> str:
    """A one-time password for a freshly provisioned account."""
    return secrets.token_urlsafe(16)


async def create_account(session: AsyncSession, hasher: PasswordHasher,
                         username: str, *, is_maintainer: bool = False) -> str:
    """Create an account, returning the one-time plaintext password.

    Raises ValueError if the username already exists.
    """
    repo = UserRepository(session)
    if await repo.get(username) is not None:
        raise ValueError(f"user '{username}' already exists")
    password = generate_password()
    await repo.create(username, hasher.hash(password), is_maintainer=is_maintainer)
    return password
