from mai.git.fake import FakeGitClient
from mai.git.types import CommitMeta


def _c(sha: str) -> CommitMeta:
    return CommitMeta(sha=sha, author="a", authored_at="2026-01-01T00:00:00Z",
                      committer="a", committed_at="2026-01-01T00:00:00Z",
                      message=sha, parents=[], is_merge=False, patch_id=f"p-{sha}")


async def test_fake_new_commits_returns_all_when_since_none():
    client = FakeGitClient({"zero": [_c("s1"), _c("s2")]})
    await client.ensure_mirror("zero", "file:///x")  # no-op
    await client.fetch("zero")                        # no-op
    metas = await client.new_commits("zero", None)
    assert [m.sha for m in metas] == ["s1", "s2"]


async def test_fake_new_commits_filters_after_since_sha():
    client = FakeGitClient({"zero": [_c("s1"), _c("s2"), _c("s3")]})
    metas = await client.new_commits("zero", "s1")
    assert [m.sha for m in metas] == ["s2", "s3"]


async def test_fake_new_commits_returns_all_when_since_unknown():
    client = FakeGitClient({"zero": [_c("s1"), _c("s2")]})
    metas = await client.new_commits("zero", "deadbeef")
    assert [m.sha for m in metas] == ["s1", "s2"]
