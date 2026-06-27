import shutil, subprocess
from pathlib import Path
import pytest
from mai.git.client import LocalGitClient

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")

def _git(cwd, *a): subprocess.run(["git", *a], cwd=cwd, check=True,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def _repo(path):
    path.mkdir(); _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@t.t"); _git(path, "config", "user.name", "T")
    _git(path, "config", "commit.gpgsign", "false"); _git(path, "config", "core.autocrlf", "false")
    (path / "f.txt").write_bytes(b"a\nb\nc\nd\ne\nf\ng\nh\n")
    _git(path, "add", "f.txt"); _git(path, "commit", "-q", "-m", "base on db layer")

TWO_HUNK = ("diff --git a/f.txt b/f.txt\n--- a/f.txt\n+++ b/f.txt\n"
            "@@ -1,3 +1,4 @@\n a\n b\n+INSERTED\n c\n"
            "@@ -6,3 +7,3 @@\n WRONGF\n g\n-h\n+H\n")

async def _client(tmp_path):
    src = tmp_path / "src"; _repo(src)
    c = LocalGitClient(str(tmp_path / "mirrors"), str(tmp_path / "worktrees"))
    await c.ensure_mirror("c", src.as_uri())
    return c

async def test_rejected_hunks_returns_rej_text(tmp_path):
    c = await _client(tmp_path)
    rej = await c.rejected_hunks("c", TWO_HUNK, ["f.txt"])
    assert "f.txt" in rej
    assert "@@" in rej["f.txt"] and "WRONGF" in rej["f.txt"]   # the rejected hunk

async def test_read_region_slices_target(tmp_path):
    c = await _client(tmp_path)
    assert await c.read_region("c", "f.txt", 2, 4) == "b\nc\nd"
    assert await c.read_region("c", "nope.txt", 1, 3) == ""

async def test_log_touching_finds_commits(tmp_path):
    c = await _client(tmp_path)
    rows = await c.log_touching("c", ["f.txt"])
    assert rows and rows[0]["title"] == "base on db layer"
    assert len(rows[0]["sha"]) == 10 and len(rows[0]["date"]) == 10
    assert await c.log_touching("c", ["nope.txt"]) == []
