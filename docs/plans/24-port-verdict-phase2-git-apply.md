# Port-Verdict Phase 2 — Git Apply Capability — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the git-worker the ability to *actually test a fix against a core's current code* — extract a commit's patch, check whether files exist on a core, maintain a per-core worktree at HEAD, and grade `git apply --check` (forward + reverse) into `clean | reverse_clean | conflict | file_absent`. This is the authoritative "does it fit / is it already there / is the code present?" signal the Phase 3 verdict stage consumes.

**Architecture:** Extend `LocalGitClient` (async subprocess over the existing bare mirrors) with four methods — `diff`, `paths_exist`, `ensure_worktree`, `apply_check` — plus a non-raising `_run_raw` so apply failures can be *graded* instead of thrown. Extend the `GitClient` protocol and `FakeGitClient` (scriptable results) so the Phase 3 logic is testable without real git. Worktrees live under a configurable dir, checked out at HEAD and reset on demand.

**Tech Stack:** Python 3.12, async subprocess (stdlib `asyncio`), pytest. Real-git integration tests gated `skipif(git not on PATH)`. No new dependencies.

## Global Constraints

- **Phase 2 of the Port-Verdict Engine** (`docs/specs/port-verdict-engine.md` §9, §13 P2). It adds capability only; the verdict *logic* that combines apply-results with relevance is Phase 3.
- **`apply_check` grades, never raises** on a non-applying patch — a conflict/absent file is data, not an error. Only real git *errors* (repo missing, etc.) raise `GitError`.
- **Result vocabulary:** `apply_check` returns exactly one of `"clean" | "reverse_clean" | "conflict" | "file_absent"` (the spec's `ApplyResult`). `reverse_clean` means the patch reverse-applies (the change is already present).
- **Worktrees are derived data:** under a configurable `git_worktree_dir` (default `./worktrees`), gitignored (already covered by `mirrors/`-style ignore — add `worktrees/`). A worktree is checked out at the mirror's HEAD and `reset --hard` on refresh; `--check` mutates nothing.
- 4-space indent. `feat:` commits, **NO AI attribution**. Commit with `git -c user.name="r-log" commit -m "..."`.
- Follow existing patterns: `LocalGitClient._run(args, *, stdin)` runs `git *args` and raises `GitError` on non-zero; `_git(core, *args)` prepends `-C <mirror>.git`. Real-git tests use the `_make_source_repo`/`_git` helper style from `tests/test_local_git_client.py` and `pytestmark = pytest.mark.skipif(shutil.which("git") is None, ...)`. Run `python -m pytest`.

---

## File Structure

| File | Responsibility |
|------|----------------|
| `src/mai/git/client.py` (modify) | `_run_raw` (non-raising); `diff`, `paths_exist` (Task 1); `ensure_worktree`, `apply_check` (Task 2); extend `GitClient` protocol; `worktree_dir` ctor arg. |
| `src/mai/git/fake.py` (modify) | `FakeGitClient` gains `diffs`/`paths` (Task 1) and `apply_results`/worktree (Task 2). |
| `src/mai/config.py` (modify) | `git_worktree_dir` (Task 2). |
| `.gitignore` (modify) | `worktrees/` (Task 2). |
| `tests/test_git_diff_paths.py` (create) | real-git `diff` + `paths_exist` (Task 1). |
| `tests/test_git_apply_check.py` (create) | real-git `ensure_worktree` + `apply_check` grading (Task 2). |
| `tests/test_git_fake_apply.py` (create) | `FakeGitClient` scripted results (Tasks 1–2). |

---

## Task 1: `diff` + `paths_exist` (+ `_run_raw`)

**Files:**
- Modify: `src/mai/git/client.py`
- Modify: `src/mai/git/fake.py`
- Test: `tests/test_git_diff_paths.py`
- Test: `tests/test_git_fake_apply.py`

**Interfaces:**
- Produces: `LocalGitClient._run_raw(args, *, stdin=None) -> tuple[int,str,str]`; `async diff(core, sha) -> str` (the commit's unified patch); `async paths_exist(core, paths) -> dict[str,bool]`. `GitClient` protocol + `FakeGitClient` gain `diff`/`paths_exist` (Fake: `diffs={(core,sha):text}`, `paths={core:[…]}`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_git_diff_paths.py`:

```python
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
```

Create `tests/test_git_fake_apply.py`:

```python
from mai.git.fake import FakeGitClient


async def test_fake_diff_and_paths():
    fake = FakeGitClient(diffs={("two", "s1"): "PATCH"},
                         paths={"two": ["src/a.cpp"]})
    assert await fake.diff("two", "s1") == "PATCH"
    assert await fake.diff("two", "unknown") == ""
    assert await fake.paths_exist("two", ["src/a.cpp", "src/b.cpp"]) == \
        {"src/a.cpp": True, "src/b.cpp": False}
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_git_diff_paths.py tests/test_git_fake_apply.py -v`
Expected: FAIL (`LocalGitClient`/`FakeGitClient` have no `diff`/`paths_exist`).

- [ ] **Step 3: Add `_run_raw` and refactor `_run`**

In `src/mai/git/client.py`, replace the existing `_run` method with a non-raising `_run_raw` plus a thin raising `_run`:

```python
    async def _run_raw(self, args: list[str], *,
                       stdin: bytes | None = None) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(
            "git", *args, cwd=None,
            stdin=asyncio.subprocess.PIPE if stdin is not None else None,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate(input=stdin)
        return (proc.returncode, out.decode("utf-8", "replace"),
                err.decode("utf-8", "replace"))

    async def _run(self, args: list[str], *, stdin: bytes | None = None) -> str:
        rc, out, err = await self._run_raw(args, stdin=stdin)
        if rc != 0:
            raise GitError(f"git {' '.join(args)} -> {rc}: {err.strip()}")
        return out
```

(The previous `_run` had a `cwd` parameter that was always `None` at the call sites — `_git` passes `-C <path>` instead — so dropping it is safe; if any caller passed `cwd`, switch it to `-C`. Grep `cwd=` to confirm none remain.)

- [ ] **Step 4: Add `diff` and `paths_exist`**

Add to `LocalGitClient`:

```python
    async def diff(self, core: str, sha: str) -> str:
        """The commit's unified patch (same diff that feeds patch-id)."""
        return await self._git(core, "diff-tree", "--root", "-p", "-M", sha)

    async def paths_exist(self, core: str, paths: list[str]) -> dict[str, bool]:
        """Whether each path exists in the core's HEAD tree."""
        result: dict[str, bool] = {}
        for p in paths:
            rc, _, _ = await self._run_raw(
                ["-C", str(self._path(core)), "cat-file", "-e", f"HEAD:{p}"])
            result[p] = rc == 0
        return result
```

- [ ] **Step 5: Extend the protocol + Fake**

In `client.py`, add to the `GitClient` Protocol:

```python
    async def diff(self, core: str, sha: str) -> str: ...
    async def paths_exist(self, core: str, paths: list[str]) -> dict[str, bool]: ...
```

In `src/mai/git/fake.py`, extend `FakeGitClient.__init__` and add the methods:

```python
    def __init__(self, commits: dict[str, list[CommitMeta]] | None = None, *,
                 diffs: dict[tuple[str, str], str] | None = None,
                 paths: dict[str, list[str]] | None = None,
                 apply_results: dict[tuple[str, str, bool], str] | None = None):
        self._commits = commits or {}
        self._diffs = diffs or {}
        self._paths = paths or {}
        self._apply = apply_results or {}

    async def diff(self, core: str, sha: str) -> str:
        return self._diffs.get((core, sha), "")

    async def paths_exist(self, core: str, paths: list[str]) -> dict[str, bool]:
        have = set(self._paths.get(core, []))
        return {p: p in have for p in paths}
```

(Keep the existing `ensure_mirror`/`fetch`/`new_commits`. The `apply_results` field is used in Task 2.)

- [ ] **Step 6: Run tests + full suite**

Run: `python -m pytest tests/test_git_diff_paths.py tests/test_git_fake_apply.py -v`
Expected: PASS.
Run: `python -m pytest -q`
Expected: green (the `_run`→`_run_raw` refactor preserves behavior; existing `test_local_git_client.py` + `test_git_harvest.py` still pass).

- [ ] **Step 7: Commit**

```bash
git add src/mai/git/client.py src/mai/git/fake.py tests/test_git_diff_paths.py tests/test_git_fake_apply.py
git -c user.name="r-log" commit -m "feat: git diff + paths_exist (+ non-raising _run_raw)"
```

---

## Task 2: `ensure_worktree` + `apply_check`

**Files:**
- Modify: `src/mai/git/client.py`
- Modify: `src/mai/git/fake.py`
- Modify: `src/mai/config.py`
- Modify: `.gitignore`
- Test: `tests/test_git_apply_check.py`
- Test: `tests/test_git_fake_apply.py` (add)

**Interfaces:**
- Consumes: `_run_raw`, mirrors (Task 1).
- Produces: `LocalGitClient.__init__(mirror_dir, worktree_dir=None)`; `async ensure_worktree(core) -> str`; `async apply_check(core, patch_text, *, reverse=False) -> str` (∈ clean/reverse_clean/conflict/file_absent). `GitClient` protocol + `FakeGitClient` gain both (Fake: `apply_results={(core,patch,reverse):result}`, default forward→`clean`, reverse→`conflict`). `settings.git_worktree_dir`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_git_apply_check.py`:

```python
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
    (path / "a.txt").write_text("one\ntwo\n")
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
    return client


async def test_ensure_worktree_checks_out_head(tmp_path):
    client = await _client(tmp_path)
    wt = await client.ensure_worktree("c")
    from pathlib import Path
    assert (Path(wt) / "a.txt").read_text() == "one\ntwo\n"


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
```

Add to `tests/test_git_fake_apply.py`:

```python
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
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_git_apply_check.py tests/test_git_fake_apply.py -v`
Expected: FAIL (no `ensure_worktree`/`apply_check`).

- [ ] **Step 3: Config + gitignore**

In `src/mai/config.py`, add after `git_mirror_dir`:

```python
    git_worktree_dir: str = "./worktrees"
```

In `.gitignore`, add a line `worktrees/` (next to `mirrors/`).

- [ ] **Step 4: Add the worktree dir to the client**

In `src/mai/git/client.py`, change `LocalGitClient.__init__`:

```python
    def __init__(self, mirror_dir: str, worktree_dir: str | None = None):
        self._root = Path(mirror_dir)
        self._wt_root = (Path(worktree_dir) if worktree_dir
                         else self._root.parent / "worktrees")
```

- [ ] **Step 5: Implement `ensure_worktree` and `apply_check`**

Add to `LocalGitClient`:

```python
    async def ensure_worktree(self, core: str) -> str:
        """A working tree of the bare mirror checked out at HEAD (reset on refresh).

        Returns the worktree path. The bare mirror shares its object store with the
        worktree, so after a fetch the new objects are present and the worktree is
        reset to the mirror's current HEAD.
        """
        head = (await self._git(core, "rev-parse", "HEAD")).strip()
        wt = self._wt_root / core
        if (wt / ".git").exists():
            await self._run(["-C", str(wt), "reset", "--hard", head])
            await self._run(["-C", str(wt), "clean", "-fdq"])
        else:
            wt.parent.mkdir(parents=True, exist_ok=True)
            await self._git(core, "worktree", "add", "--detach", "--force",
                            str(wt), head)
        return str(wt)

    async def apply_check(self, core: str, patch_text: str, *,
                          reverse: bool = False) -> str:
        """Grade `git apply --check` of a patch against the core's worktree.

        Returns 'clean' | 'reverse_clean' | 'conflict' | 'file_absent'. Never raises
        on a non-applying patch — the result is the signal.
        """
        wt = await self.ensure_worktree(core)
        args = ["-C", wt, "apply", "--check"]
        if reverse:
            args.append("--reverse")
        args.append("-")
        rc, _, err = await self._run_raw(args, stdin=patch_text.encode("utf-8", "replace"))
        if rc == 0:
            return "reverse_clean" if reverse else "clean"
        low = err.lower()
        if "no such file" in low or "does not exist" in low:
            return "file_absent"
        return "conflict"
```

- [ ] **Step 6: Extend the protocol + Fake**

In the `GitClient` Protocol add:

```python
    async def ensure_worktree(self, core: str) -> str: ...
    async def apply_check(self, core: str, patch_text: str, *,
                          reverse: bool = False) -> str: ...
```

In `src/mai/git/fake.py` add to `FakeGitClient`:

```python
    async def ensure_worktree(self, core: str) -> str:
        return f"/fake/worktrees/{core}"

    async def apply_check(self, core: str, patch_text: str, *,
                          reverse: bool = False) -> str:
        key = (core, patch_text, reverse)
        if key in self._apply:
            return self._apply[key]
        return "conflict" if reverse else "clean"
```

- [ ] **Step 7: Run tests + full suite**

Run: `python -m pytest tests/test_git_apply_check.py tests/test_git_fake_apply.py -v`
Expected: PASS (real-git grading: clean/conflict/file_absent/reverse_clean).
Run: `python -m pytest -q`
Expected: full suite green.

- [ ] **Step 8: Commit**

```bash
git add src/mai/git/client.py src/mai/git/fake.py src/mai/config.py .gitignore tests/test_git_apply_check.py tests/test_git_fake_apply.py
git -c user.name="r-log" commit -m "feat: per-core worktrees + graded git apply-check"
```

---

## Self-Review

**Spec coverage (`port-verdict-engine.md` §9 P2 interfaces, §13 P2):**
- `GitClient` gains `ensure_worktree`/`apply_check`(+reverse)/`paths_exist`/`diff` → Tasks 1–2.
- `ApplyResult ∈ {clean, conflict, file_absent, reverse_clean}` → `apply_check` returns exactly these.
- `FakeGitClient` scripted results → Tasks 1–2 (`diffs`/`paths`/`apply_results`).
- Worktree-per-core at HEAD, refreshed → `ensure_worktree` (reset --hard + clean on refresh).
- "apply grades, never raises" → `_run_raw` + `apply_check` returns a string for non-zero.

**Deliberately deferred (not gaps):** the verdict stage that *combines* apply-results with the Phase 1 relevance class into NEEDS/REVIEW/N-A/HAS-IT (Phase 3); the board (Phase 4). Phase 2 is pure capability — nothing calls `apply_check` in the pipeline yet.

**Placeholder scan:** none — every step is complete, including the hand-crafted patch fixtures (valid unified diffs) the integration tests apply.

**Type consistency:** `apply_check(core, patch_text, *, reverse=False) -> str` and `diff(core, sha) -> str`, `paths_exist(core, paths) -> dict[str,bool]`, `ensure_worktree(core) -> str` are identical across the protocol, `LocalGitClient`, and `FakeGitClient`. `_run_raw(args, *, stdin) -> tuple[int,str,str]` is used by `_run`, `paths_exist`, and `apply_check`. `FakeGitClient.__init__` keyword fields (`diffs`/`paths`/`apply_results`) match the test usages and the Phase-3 consumer.

**Risk note:** `apply_check`'s `file_absent` vs `conflict` split keys on git's stderr wording (`"no such file"` / `"does not exist"`). The real-git integration test pins both branches; if a future git version reworded these, the test catches it. Binary patches fall to `conflict` (→ REVIEW in Phase 3), which is the intended honest treatment.

---

## Execution Handoff

After saving, choose execution mode:
1. **Subagent-Driven (recommended)** — a fresh subagent per task, two-stage review between tasks.
2. **Inline Execution** — execute in this session with checkpoints.
