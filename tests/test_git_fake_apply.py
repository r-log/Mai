from mai.git.fake import FakeGitClient


async def test_fake_diff_and_paths():
    fake = FakeGitClient(diffs={("two", "s1"): "PATCH"},
                         paths={"two": ["src/a.cpp"]})
    assert await fake.diff("two", "s1") == "PATCH"
    assert await fake.diff("two", "unknown") == ""
    assert await fake.paths_exist("two", ["src/a.cpp", "src/b.cpp"]) == \
        {"src/a.cpp": True, "src/b.cpp": False}


async def test_fake_apply_check_defaults_and_scripted():
    fake = FakeGitClient(apply_results={
        ("two", "PATCH", False): "conflict",
        ("two", "PATCH", True): "reverse_clean",
    })
    # scripted
    assert await fake.apply_check("two", "PATCH") == "conflict"
    assert await fake.apply_check("two", "PATCH", reverse=True) == "reverse_clean"
    # defaults: forward -> clean, reverse -> conflict (not already present)
    assert await fake.apply_check("two", "OTHER") == "clean"
    assert await fake.apply_check("two", "OTHER", reverse=True) == "conflict"
    assert await fake.ensure_worktree("two") == "/fake/worktrees/two"
