from mai.git.fake import FakeGitClient


async def test_fake_apply_fraction_scripted_and_default():
    fake = FakeGitClient(fractions={("two", "P"): (3, 4)})
    assert await fake.apply_fraction("two", "P", ["x"]) == (3, 4)
    assert await fake.apply_fraction("two", "OTHER", ["x"]) == (0, 1)
