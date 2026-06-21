import pytest

from mai.auth.accounts import create_account, generate_password
from mai.auth.fake import FakeHasher
from mai.repository.users import UserRepository


def test_generate_password_is_long_and_urlsafe():
    pw = generate_password()
    assert len(pw) >= 16
    assert pw.isascii() and " " not in pw


async def test_create_account_makes_must_change_user(session):
    hasher = FakeHasher()
    pw = await create_account(session, hasher, "antz", is_maintainer=True)
    await session.commit()
    user = await UserRepository(session).get("antz")
    assert user is not None
    assert user.is_maintainer is True
    assert user.must_change_password is True
    assert hasher.verify(pw, user.password_hash)   # returned pw matches stored hash
    assert pw not in user.password_hash            # stored value is not the plaintext


async def test_create_account_rejects_duplicate(session):
    hasher = FakeHasher()
    await create_account(session, hasher, "dup")
    await session.commit()
    with pytest.raises(ValueError):
        await create_account(session, hasher, "dup")
