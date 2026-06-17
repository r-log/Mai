import shutil
import subprocess

import pytest

from mai.git.client import LocalGitClient

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _make_source_repo(path):
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
    _git(path, "mv", "a.txt", "b.txt")
    _git(path, "commit", "-q", "-m", "rename a to b")


async def test_local_client_harvests_real_commits(tmp_path):
    src = tmp_path / "src"
    _make_source_repo(src)
    client = LocalGitClient(str(tmp_path / "mirrors"))

    await client.ensure_mirror("test", src.as_uri())
    await client.fetch("test")
    metas = await client.new_commits("test", None)

    assert [m.message for m in metas] == ["add a", "grow a", "rename a to b"]
    assert all(m.patch_id for m in metas)           # every non-merge commit has a patch-id
    # rename detected on the third commit
    rename_files = metas[2].files
    assert any(f.change_type == "R" and f.old_path == "a.txt" and f.path == "b.txt"
               for f in rename_files)
    # second commit added exactly one line
    assert metas[1].files[0].added == 1


async def test_local_client_patch_id_is_deterministic(tmp_path):
    src = tmp_path / "src"
    _make_source_repo(src)
    client = LocalGitClient(str(tmp_path / "mirrors"))
    await client.ensure_mirror("test", src.as_uri())
    first = await client.new_commits("test", None)
    second = await client.new_commits("test", None)   # re-walk
    assert [m.patch_id for m in first] == [m.patch_id for m in second]


async def test_local_client_incremental_since_sha(tmp_path):
    src = tmp_path / "src"
    _make_source_repo(src)
    client = LocalGitClient(str(tmp_path / "mirrors"))
    await client.ensure_mirror("test", src.as_uri())
    metas = await client.new_commits("test", None)
    tail = await client.new_commits("test", metas[0].sha)
    assert [m.message for m in tail] == ["grow a", "rename a to b"]
