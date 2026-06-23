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
    _git(path, "config", "core.autocrlf", "false")
    (path / "f.txt").write_bytes(b"a\nb\nc\nd\ne\nf\ng\nh\n")
    _git(path, "add", "f.txt")
    _git(path, "commit", "-q", "-m", "base")


# hunk 1 (top) applies; hunk 2 (bottom) has wrong context -> rejects
TWO_HUNK = (
    "diff --git a/f.txt b/f.txt\n--- a/f.txt\n+++ b/f.txt\n"
    "@@ -1,3 +1,4 @@\n a\n b\n+INSERTED\n c\n"
    "@@ -6,3 +7,3 @@\n WRONGF\n g\n-h\n+H\n"
)


async def _client(tmp_path):
    src = tmp_path / "src"
    _repo(src)
    c = LocalGitClient(str(tmp_path / "mirrors"), str(tmp_path / "worktrees"))
    await c.ensure_mirror("c", src.as_uri())
    return c


async def test_apply_fraction_counts_applied_hunks(tmp_path):
    c = await _client(tmp_path)
    applied, total = await c.apply_fraction("c", TWO_HUNK, ["f.txt"])
    assert (applied, total) == (1, 2)        # 1 of 2 hunks applies


async def test_apply_fraction_no_hunks_is_zero(tmp_path):
    c = await _client(tmp_path)
    assert await c.apply_fraction("c", "Binary files differ\n", ["f.txt"]) == (0, 0)
