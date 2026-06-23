"""LocalGitClient perf-path correctness: ensure_worktree skips reset when the
worktree is already clean, but resets after apply_fraction dirties it; paths_exist
is batched into one spawn. These guard the optimization that keeps verdicts honest
while cutting git spawns on Windows."""
import shutil
import subprocess
from pathlib import Path

import pytest

from mai.git.client import LocalGitClient

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")

_BASE = b"a\nb\nc\nd\ne\nf\ng\nh\n"
# hunk 1 (top) applies; hunk 2 (bottom) has wrong context -> rejects
TWO_HUNK = (
    "diff --git a/f.txt b/f.txt\n--- a/f.txt\n+++ b/f.txt\n"
    "@@ -1,3 +1,4 @@\n a\n b\n+INSERTED\n c\n"
    "@@ -6,3 +7,3 @@\n WRONGF\n g\n-h\n+H\n"
)


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
    (path / "f.txt").write_bytes(_BASE)
    _git(path, "add", "f.txt")
    _git(path, "commit", "-q", "-m", "base")


async def _client(tmp_path):
    src = tmp_path / "src"
    _repo(src)
    c = LocalGitClient(str(tmp_path / "mirrors"), str(tmp_path / "worktrees"))
    await c.ensure_mirror("c", src.as_uri())
    return c


async def test_ensure_worktree_skips_reset_when_clean(tmp_path):
    c = await _client(tmp_path)
    wt = Path(await c.ensure_worktree("c"))
    sentinel = wt / "sentinel.tmp"          # untracked: a reset+clean would remove it
    sentinel.write_text("x")
    await c.ensure_worktree("c")            # clean hot-path: must NOT clean
    assert sentinel.exists()


async def test_apply_fraction_dirties_then_next_ensure_resets(tmp_path):
    c = await _client(tmp_path)
    await c.apply_fraction("c", TWO_HUNK, ["f.txt"])  # applies hunk1, writes f.txt.rej
    wt = Path(await c.ensure_worktree("c"))           # dirty -> must reset+clean
    assert not (wt / "f.txt.rej").exists()            # untracked .rej cleaned
    assert (wt / "f.txt").read_bytes() == _BASE       # tracked modification reverted


async def test_paths_exist_batched(tmp_path):
    c = await _client(tmp_path)
    assert await c.paths_exist("c", ["f.txt", "nope.txt"]) == {
        "f.txt": True, "nope.txt": False}
    assert await c.paths_exist("c", []) == {}
