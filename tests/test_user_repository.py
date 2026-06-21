import pytest

from mai.repository.users import UserRepository


async def test_create_and_get(session):
    repo = UserRepository(session)
    await repo.create("antz", "hash1", is_maintainer=True)
    await session.commit()
    user = await repo.get("antz")
    assert user is not None
    assert user.username == "antz"
    assert user.password_hash == "hash1"
    assert user.is_maintainer is True
    assert user.must_change_password is True  # fresh accounts must change
    assert user.display_name == "antz"        # defaults to username


async def test_get_unknown_returns_none(session):
    assert await UserRepository(session).get("nobody") is None


async def test_set_password_clears_must_change(session):
    repo = UserRepository(session)
    user = await repo.create("madmax", "old")
    await session.commit()
    await repo.set_password(user, "new")
    await session.commit()
    refreshed = await repo.get("madmax")
    assert refreshed.password_hash == "new"
    assert refreshed.must_change_password is False


async def test_all_sorted_by_username(session):
    repo = UserRepository(session)
    await repo.create("zeb", "h")
    await repo.create("ana", "h")
    await session.commit()
    assert [u.username for u in await repo.all()] == ["ana", "zeb"]
