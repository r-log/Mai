import shutil
import subprocess

import pytest

from mai.db.models import (Commit, CommitFile, PatchGroup, Propagation,
                           SubsystemClass)
from mai.git.client import LocalGitClient
from mai.repository.port_verdict import PortVerdictRepository
from mai.sync.verdicts import compute_verdicts

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _repo(path):
    path.mkdir(parents=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@t.t")
    _git(path, "config", "user.name", "T")
    _git(path, "config", "commit.gpgsign", "false")
    _git(path, "config", "core.autocrlf", "false")
    sub = path / "src" / "shared"
    sub.mkdir(parents=True)
    (sub / "log.cpp").write_bytes(b"a\nb\n")
    _git(path, "add", ".")
    _git(path, "commit", "-q", "-m", "base")


async def test_golden_shared_fix_is_needs_on_real_git(session, tmp_path):
    # target core 'two' has src/shared/log.cpp = "a\nb\n"; the fix adds a line cleanly.
    two = tmp_path / "two"
    _repo(two)
    client = LocalGitClient(str(tmp_path / "mirrors"), str(tmp_path / "worktrees"))
    await client.ensure_mirror("two", two.as_uri())
    base = await client.head_sha("two")

    # a fix that exists on 'three' (source), absent on 'two'; the patch adds "c".
    patch = ("diff --git a/src/shared/log.cpp b/src/shared/log.cpp\n"
             "--- a/src/shared/log.cpp\n+++ b/src/shared/log.cpp\n"
             "@@ -1,2 +1,3 @@\n a\n b\n+c\n")

    session.add(PatchGroup(id="pgX", patch_id="px"))
    session.add(SubsystemClass(subsystem="src/shared", classification="shared",
                               source="heuristic"))
    session.add(Propagation(patch_group_id="pgX", core="three", present=True,
                            source_sha="srcsha"))
    session.add(Propagation(patch_group_id="pgX", core="two", present=False,
                            source_sha=None))
    c = Commit(core="three", sha="srcsha", author="a", authored_at="t", committer="a",
               committed_at="t", message="add c", parent_shas=["p"], is_merge=False)
    session.add(c)
    await session.flush()
    session.add(CommitFile(commit_id=c.id, path="src/shared/log.cpp", change_type="M",
                           added_lines=1, removed_lines=0, subsystem="src/shared"))
    await session.commit()

    # the source diff must come from the git client; monkeypatch diff to our patch,
    # OR (cleaner) have the source repo too. Simplest: wrap the client so diff() returns
    # the known patch for the source.
    class _Client(LocalGitClient):
        async def diff(self, core, sha):
            return patch if (core, sha) == ("three", "srcsha") else await super().diff(core, sha)

    wrapped = _Client(str(tmp_path / "mirrors"), str(tmp_path / "worktrees"))
    counts = await compute_verdicts(session, wrapped)
    v = await PortVerdictRepository(session).get("pgX", "two")
    assert v.verdict == "needs"          # clean apply to two + shared = NEEDS
    assert v.base_sha == base
    assert counts["needs"] >= 1
