import pytest

from mai.auth.fake import FakeHasher
from mai.auth.hasher import Argon2Hasher


def test_argon2_roundtrip():
    h = Argon2Hasher()
    hashed = h.hash("correct horse")
    assert hashed != "correct horse"          # never stored in clear
    assert h.verify("correct horse", hashed) is True
    assert h.verify("wrong", hashed) is False


def test_argon2_verify_rejects_garbage_hash():
    assert Argon2Hasher().verify("x", "not-a-valid-hash") is False


def test_fake_hasher_roundtrip():
    h = FakeHasher()
    hashed = h.hash("pw")
    assert h.verify("pw", hashed) is True
    assert h.verify("nope", hashed) is False
