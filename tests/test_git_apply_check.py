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
    (path / "a.txt").write_bytes(b"one\ntwo\n")
    _git(path, "add", "a.txt")
    _git(path, "commit", "-q", "-m", "add a")


CLEAN = (
    "diff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ b/a.txt\n"
    "@@ -1,2 +1,3 @@\n one\n two\n+three\n"
)
CONFLICT = (
    "diff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ b/a.txt\n"
    "@@ -1,2 +1,3 @@\n WRONG\n two\n+three\n"
)
ABSENT = (
    "diff --git a/ghost.txt b/ghost.txt\n--- a/ghost.txt\n+++ b/ghost.txt\n"
    "@@ -1 +1,2 @@\n x\n+y\n"
)


async def _client(tmp_path):
    src = tmp_path / "src"
    _repo(src)
    client = LocalGitClient(str(tmp_path / "mirrors"), str(tmp_path / "worktrees"))
    await client.ensure_mirror("c", src.as_uri())
    # Disable CRLF conversion so worktree checkouts and apply-check patches
    # keep LF on Windows, matching the bytes we wrote with write_bytes().
    mirror = tmp_path / "mirrors" / "c.git"
    _git(mirror, "config", "core.autocrlf", "false")
    return client


async def test_ensure_worktree_checks_out_head(tmp_path):
    client = await _client(tmp_path)
    wt = await client.ensure_worktree("c")
    from pathlib import Path
    assert (Path(wt) / "a.txt").read_bytes() == b"one\ntwo\n"


async def test_apply_check_clean(tmp_path):
    client = await _client(tmp_path)
    assert await client.apply_check("c", CLEAN) == "clean"


async def test_apply_check_conflict(tmp_path):
    client = await _client(tmp_path)
    assert await client.apply_check("c", CONFLICT) == "conflict"


async def test_apply_check_file_absent(tmp_path):
    client = await _client(tmp_path)
    assert await client.apply_check("c", ABSENT) == "file_absent"


async def test_apply_check_reverse_clean_when_already_present(tmp_path):
    client = await _client(tmp_path)
    # a patch whose post-image ("one\ntwo") is already the worktree state reverse-applies
    already = (
        "diff --git a/a.txt b/a.txt\n--- a/a.txt\n+++ b/a.txt\n"
        "@@ -1 +1,2 @@\n one\n+two\n"
    )
    assert await client.apply_check("c", already, reverse=True) == "reverse_clean"
