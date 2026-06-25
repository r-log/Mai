"""Acceptance golden — the #229 fixture against REAL core checkouts, plus a real
positive control built from a minimal fixture repo (no faked verdicts).

The #229 cases are skipped if the local bare mirrors are not present (CI without the
~350MB/core mirrors); the positive control is fully self-contained and always runs.
Run with:  python -m pytest tests/test_acceptance_portability.py -s
"""
import subprocess
from pathlib import Path

import pytest

from mai.git.client import LocalGitClient
from mai.portability.classifier import evaluate
from mai.portability.types import State

REPO = Path(__file__).resolve().parents[1]
MIRRORS = REPO / "mirrors"
PR229_SHA = "78b7a9951"
DBC_PATH = "src/shared/DataStores/DBCFileLoader.cpp"

_mirrors_present = all((MIRRORS / f"{c}.git" / "HEAD").exists()
                       for c in ("three", "zero", "one", "two"))


@pytest.mark.skipif(not _mirrors_present, reason="local core mirrors not present")
@pytest.mark.parametrize("target", ["zero", "one", "two"])
async def test_229_not_applicable_to_pre_multilocale_cores(target, capsys):
    client = LocalGitClient(str(MIRRORS), str(REPO / "worktrees"))
    v = await evaluate(client, source_core="three", source_sha=PR229_SHA,
                       target_core=target)
    with capsys.disabled():
        print(f"\n=== evaluate(#229, {target}) ===")
        print(v.as_dict())
    assert v.state is State.NOT_APPLICABLE
    blob = " ".join(e.detail for e in v.evidence)
    assert "loc" in blob and "AutoProduceStrings" in blob


def _git(args, cwd):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "-c", "core.autocrlf=false", *args],
                   cwd=cwd, check=True, capture_output=True)


def _build_pair(tmp: Path) -> tuple[Path, str]:
    """A source mirror with a fix on top of a base, and a target mirror at the base.

    The fix adds a function that references `helper`, which the target already has —
    so it applies cleanly AND has all required symbols => PORTABLE.
    """
    mirrors = tmp / "m"
    mirrors.mkdir()
    work = tmp / "work"
    work.mkdir()
    _git(["init", "-q", "-b", "main"], work)
    util = work / "util.cpp"
    util.write_text("int helper() { return 1; }\n", encoding="utf-8")
    _git(["add", "util.cpp"], work)
    _git(["commit", "-qm", "base"], work)
    _git(["clone", "-q", "--bare", str(work), str(mirrors / "tgt.git")], tmp)

    util.write_text("int helper() { return 1; }\n"
                    "int added() { return helper() + 1; }\n", encoding="utf-8")
    _git(["commit", "-qam", "add shared helper caller"], work)
    _git(["clone", "-q", "--bare", str(work), str(mirrors / "src.git")], tmp)
    sha = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"],
                         check=True, capture_output=True, text=True).stdout.strip()
    return mirrors, sha


async def test_positive_control_portable(tmp_path, capsys):
    mirrors, sha = _build_pair(tmp_path)
    client = LocalGitClient(str(mirrors), str(tmp_path / "wt"))
    v = await evaluate(client, source_core="src", source_sha=sha, target_core="tgt")
    with capsys.disabled():
        print("\n=== positive control evaluate(shared-helper-add, tgt) ===")
        print(v.as_dict())
    assert v.state is State.PORTABLE
