import shutil
import subprocess

import pytest

from mai.git.client import LocalGitClient

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _repo(path):
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@t.t")
    _git(path, "config", "user.name", "T")
    _git(path, "config", "commit.gpgsign", "false")
    (path / "a.txt").write_text("one\n")
    _git(path, "add", "a.txt")
    _git(path, "commit", "-q", "-m", "add a")
    (path / "a.txt").write_text("one\ntwo\n")
    _git(path, "add", "a.txt")
    _git(path, "commit", "-q", "-m", "grow a")


async def test_diff_returns_the_commit_patch(tmp_path):
    src = tmp_path / "src"
    _repo(src)
    client = LocalGitClient(str(tmp_path / "mirrors"))
    await client.ensure_mirror("c", src.as_uri())
    metas = await client.new_commits("c", None)
    patch = await client.diff("c", metas[1].sha)      # the "grow a" commit
    assert "a.txt" in patch
    assert "+two" in patch                            # the added line is in the diff


async def test_paths_exist_on_head(tmp_path):
    src = tmp_path / "src"
    _repo(src)
    client = LocalGitClient(str(tmp_path / "mirrors"))
    await client.ensure_mirror("c", src.as_uri())
    result = await client.paths_exist("c", ["a.txt", "ghost.txt"])
    assert result == {"a.txt": True, "ghost.txt": False}
