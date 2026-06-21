import hashlib


class FakeHasher:
    """Deterministic, fast, NOT secure. Tests only."""

    def hash(self, password: str) -> str:
        digest = hashlib.sha256(password.encode()).hexdigest()
        return f"fake${digest}"

    def verify(self, password: str, hashed: str) -> bool:
        return hashed == self.hash(password)
