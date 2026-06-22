from mai.git.fake import FakeGitClient


async def test_fake_diff_and_paths():
    fake = FakeGitClient(diffs={("two", "s1"): "PATCH"},
                         paths={"two": ["src/a.cpp"]})
    assert await fake.diff("two", "s1") == "PATCH"
    assert await fake.diff("two", "unknown") == ""
    assert await fake.paths_exist("two", ["src/a.cpp", "src/b.cpp"]) == \
        {"src/a.cpp": True, "src/b.cpp": False}
