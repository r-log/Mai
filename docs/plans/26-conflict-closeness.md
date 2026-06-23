# Conflict Closeness (applied-hunk fraction) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every `review`-conflict verdict a sortable closeness — `applied / total` hunks — so the REVIEW lane ranks near-portable fixes above hard ones, without touching the truthfulness gate.

**Architecture:** A new `apply_fraction(core, patch, paths)` on the git client measures how many of a patch's hunks land via `git apply --reject` (count `@@` in the patch vs in the `.rej` files for the touched paths). `compute_verdicts` calls it only in the conflict branch, stores `conflict_applied`/`conflict_total` on `PortVerdict`, and labels evidence `near/partial/far`.

**Tech Stack:** Python 3.12, async subprocess, pytest (+ real-git `skipif`). No new dependencies.

## Global Constraints

- Spec: `docs/specs/conflict-closeness.md`. Builds on the Port-Verdict engine.
- **Truthfulness gate untouched:** NEEDS still = clean apply AND all-shared. Closeness only annotates `review`-conflict verdicts; it never changes a verdict.
- **Only conflict verdicts carry a fraction.** `clean`/`has_it`/`not_applicable` leave `conflict_applied`/`conflict_total` null.
- **Bounded I/O:** count `.rej` only for the patch's touched `paths` — never a recursive worktree scan. `apply_fraction` never raises on a non-applying patch (uses `_run_raw`).
- **Thresholds:** label = `near` (fraction ≥ 0.8) / `partial` (≥ 0.4) / `far` (< 0.4).
- The `--reject` apply dirties the worktree; the next fix's `ensure_worktree` reset+clean already wipes it — no extra reset.
- 4-space indent. `feat:` commits, **NO AI attribution**. Commit with `git -c user.name="r-log" commit -m "..."`.
- Follow existing patterns: `LocalGitClient` (`_run_raw`, `_git`, `ensure_worktree`, `Path` imported); `FakeGitClient` keyword-scriptable; `PortVerdict` model + `PortVerdictRepository.upsert(**fields)`; `compute_verdicts` in `src/mai/sync/verdicts.py`. Real-git tests `skipif(shutil.which("git") is None)`, write fixtures with `write_bytes` + `core.autocrlf=false` for Windows LF determinism. Run `python -m pytest`.

---

## File Structure

| File | Responsibility |
|------|----------------|
| `src/mai/git/client.py` (modify) | `apply_fraction` on protocol + `LocalGitClient`. |
| `src/mai/git/fake.py` (modify) | `FakeGitClient.apply_fraction` + `fractions` kwarg. |
| `src/mai/db/models.py` (modify) | `PortVerdict.conflict_applied` / `conflict_total`. |
| `src/mai/sync/verdicts.py` (modify) | `closeness_label` + conflict-branch integration. |
| `tests/test_apply_fraction.py` (create) | real-git partial-apply + fake. |
| `tests/test_conflict_closeness.py` (create) | compute_verdicts records fraction; non-conflict null; label thresholds. |

---

## Task 1: `apply_fraction` on the git client

**Files:**
- Modify: `src/mai/git/client.py`, `src/mai/git/fake.py`
- Test: `tests/test_apply_fraction.py`

**Interfaces:**
- Produces: `GitClient.apply_fraction(core, patch_text, paths) -> tuple[int,int]` (+ `LocalGitClient` impl + `FakeGitClient` impl with `fractions={(core,patch):(applied,total)}` kwarg, default `(0,1)`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_apply_fraction.py`:

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
```

Create `tests/test_fake_apply_fraction.py`:

```python
from mai.git.fake import FakeGitClient


async def test_fake_apply_fraction_scripted_and_default():
    fake = FakeGitClient(fractions={("two", "P"): (3, 4)})
    assert await fake.apply_fraction("two", "P", ["x"]) == (3, 4)
    assert await fake.apply_fraction("two", "OTHER", ["x"]) == (0, 1)
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_apply_fraction.py tests/test_fake_apply_fraction.py -v`
Expected: FAIL (`apply_fraction` missing).

- [ ] **Step 3: Implement `apply_fraction` on `LocalGitClient`**

In `src/mai/git/client.py`, add to the `GitClient` Protocol:

```python
    async def apply_fraction(self, core: str, patch_text: str,
                             paths: list[str]) -> tuple[int, int]: ...
```

Add to `LocalGitClient`:

```python
    async def apply_fraction(self, core: str, patch_text: str,
                             paths: list[str]) -> tuple[int, int]:
        """How much of a (conflicting) patch applies, in hunks: (applied, total).

        Runs `git apply --reject` (lands clean hunks, writes <file>.rej for the rest),
        then counts hunk headers in the patch vs in the .rej files for `paths`.
        total == 0 (binary / no hunks) -> (0, 0). Never raises on a non-applying patch.
        """
        total = sum(1 for ln in patch_text.splitlines() if ln.startswith("@@ "))
        if total == 0:
            return (0, 0)
        wt = await self.ensure_worktree(core)
        await self._run_raw(["-C", wt, "apply", "--reject", "-"],
                            stdin=patch_text.encode("utf-8", "replace"))
        rejected = 0
        for p in paths:
            rej = Path(wt) / (p + ".rej")
            if rej.exists():
                rejected += sum(1 for ln in rej.read_text("utf-8", "replace").splitlines()
                                if ln.startswith("@@ "))
        return (max(0, total - rejected), total)
```

- [ ] **Step 4: Implement `FakeGitClient.apply_fraction`**

In `src/mai/git/fake.py`, add a keyword `fractions: dict[tuple[str, str], tuple[int, int]] | None = None`
to `__init__` (store `self._fractions = fractions or {}`), and:

```python
    async def apply_fraction(self, core: str, patch_text: str,
                             paths: list[str]) -> tuple[int, int]:
        return self._fractions.get((core, patch_text), (0, 1))
```

- [ ] **Step 5: Run tests + full suite**

Run: `python -m pytest tests/test_apply_fraction.py tests/test_fake_apply_fraction.py -v`
Expected: PASS (the real-git test asserts `(1, 2)`).
Run: `python -m pytest -q`
Expected: green.

- [ ] **Step 6: Commit**

```bash
git add src/mai/git/client.py src/mai/git/fake.py tests/test_apply_fraction.py tests/test_fake_apply_fraction.py
git -c user.name="r-log" commit -m "feat: git apply_fraction (applied/total hunks of a conflicting patch)"
```

---

## Task 2: store closeness on conflict verdicts

**Files:**
- Modify: `src/mai/db/models.py`, `src/mai/sync/verdicts.py`
- Test: `tests/test_conflict_closeness.py`

**Interfaces:**
- Consumes: `apply_fraction` (Task 1).
- Produces: `PortVerdict.conflict_applied` / `conflict_total` (nullable ints); `closeness_label(applied, total) -> str`; `compute_verdicts` records the fraction + evidence for conflict verdicts only.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_conflict_closeness.py`:

```python
import pytest

from mai.db.models import Commit, CommitFile, PatchGroup, Propagation, SubsystemClass
from mai.git.fake import FakeGitClient
from mai.repository.port_verdict import PortVerdictRepository
from mai.sync.verdicts import closeness_label, compute_verdicts


def test_closeness_label_thresholds():
    assert closeness_label(8, 10) == "near"      # 0.8
    assert closeness_label(4, 10) == "partial"   # 0.4
    assert closeness_label(3, 10) == "far"       # 0.3
    assert closeness_label(10, 10) == "near"


async def _fix(session, *, pg_id, subsystem, classification, source_sha):
    session.add(PatchGroup(id=pg_id, patch_id=f"p-{pg_id}"))
    session.add(SubsystemClass(subsystem=subsystem, classification=classification,
                               source="heuristic"))
    session.add(Propagation(patch_group_id=pg_id, core="three", present=True,
                            source_sha=source_sha))
    session.add(Propagation(patch_group_id=pg_id, core="two", present=False, source_sha=None))
    c = Commit(core="three", sha=source_sha, author="a", authored_at="t", committer="a",
               committed_at="t", message="fix", parent_shas=["p"], is_merge=False)
    session.add(c)
    await session.flush()
    session.add(CommitFile(commit_id=c.id, path=f"{subsystem}/x.cpp", change_type="M",
                           added_lines=2, removed_lines=0, subsystem=subsystem))
    await session.flush()


async def test_conflict_verdict_records_closeness(session):
    await _fix(session, pg_id="pgC", subsystem="src/shared/Db", classification="shared",
               source_sha="sC")
    await session.commit()
    fake = FakeGitClient(
        diffs={("three", "sC"): "PC"},
        paths={"two": ["src/shared/Db/x.cpp"]},
        apply_results={("two", "PC", False): "conflict"},
        fractions={("two", "PC"): (5, 6)})
    await compute_verdicts(session, fake)
    v = await PortVerdictRepository(session).get("pgC", "two")
    assert v.verdict == "review" and v.apply_result == "conflict"
    assert v.conflict_applied == 5 and v.conflict_total == 6
    assert any("5/6 hunks apply (near)" in e for e in v.evidence)


async def test_non_conflict_verdict_has_null_closeness(session):
    # a clean shared apply -> NEEDS, no fraction
    await _fix(session, pg_id="pgN", subsystem="src/shared/Db", classification="shared",
               source_sha="sN")
    await session.commit()
    fake = FakeGitClient(diffs={("three", "sN"): "PN"},
                         paths={"two": ["src/shared/Db/x.cpp"]})   # default forward -> clean
    await compute_verdicts(session, fake)
    v = await PortVerdictRepository(session).get("pgN", "two")
    assert v.verdict == "needs"
    assert v.conflict_applied is None and v.conflict_total is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_conflict_closeness.py -v`
Expected: FAIL (`closeness_label` missing / fields absent).

- [ ] **Step 3: Add the `PortVerdict` fields**

In `src/mai/db/models.py`, add to `PortVerdict` (after `similar_commit`):

```python
    conflict_applied: Mapped[int | None] = mapped_column(Integer, nullable=True)
    conflict_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
```

- [ ] **Step 4: Add `closeness_label` + wire into `compute_verdicts`**

In `src/mai/sync/verdicts.py`, add the helper near `resolve_relevance`:

```python
def closeness_label(applied: int, total: int) -> str:
    """Band a conflict's applied-hunk fraction: near >= 0.8, partial >= 0.4, else far."""
    frac = (applied / total) if total else 0.0
    return "near" if frac >= 0.8 else "partial" if frac >= 0.4 else "far"
```

In `compute_verdicts`, the grading sets `verdict`/`apply_result`. AFTER the grading block (where `evidence` is built) and BEFORE the `upsert`, compute the closeness for conflicts:

```python
                conflict_applied = conflict_total = None
                if verdict == "review" and apply_result == "conflict":
                    a, t = await git_client.apply_fraction(target_core, patch, paths)
                    if t > 0:
                        conflict_applied, conflict_total = a, t
                        evidence.append(
                            f"conflict: {a}/{t} hunks apply ({closeness_label(a, t)})")
```

Then add the two fields to the existing `vrepo.upsert(...)` call:

```python
                    conflict_applied=conflict_applied, conflict_total=conflict_total,
```

(Place the `conflict_applied = conflict_total = None` init and the conflict block inside the existing `try:`, right after the `evidence = [...]` list is built and before `await vrepo.upsert(...)`.)

- [ ] **Step 5: Run tests + full suite**

Run: `python -m pytest tests/test_conflict_closeness.py -v`
Expected: PASS — conflict verdict records `5/6` + `near`; the NEEDS verdict leaves both fields null.
Run: `python -m pytest -q`
Expected: full suite green (the truthfulness-gate / cache / multi-fix tests still pass; `apply_fraction` is only called in the conflict branch).

- [ ] **Step 6: Commit**

```bash
git add src/mai/db/models.py src/mai/sync/verdicts.py tests/test_conflict_closeness.py
git -c user.name="r-log" commit -m "feat: record conflict closeness (applied/total hunks + near/partial/far) on verdicts"
```

---

## Self-Review

**Spec coverage (`conflict-closeness.md`):**
- applied-hunk fraction via `git apply --reject` + `.rej` counting → Task 1 `apply_fraction`.
- `PortVerdict.conflict_applied/_total`, set only for conflict verdicts → Task 2.
- near/partial/far evidence label (0.8/0.4) → Task 2 `closeness_label`.
- truthfulness gate untouched; only the conflict branch is annotated → Task 2 (fraction computed inside `if verdict == "review" and apply_result == "conflict"`).
- bounded `.rej` reads for touched paths only; binary/no-hunk → (0,0) → null → Task 1 + edge tests.

**Deliberately out of scope (not gaps):** the board sorting/rendering by closeness (Phase 4 of the verdict engine); 3-way merge (needs cross-mirror objects).

**Placeholder scan:** none — complete code; the real-git `TWO_HUNK` patch is a valid 2-hunk diff (hunk 1 matches → applies; hunk 2 has `WRONGF` context → rejects → `.rej` has 1 hunk → (1,2)).

**Type consistency:** `apply_fraction(core, patch_text, paths) -> tuple[int,int]` identical across protocol/Local/Fake; `closeness_label(int,int) -> str`; the two nullable `PortVerdict` fields flow through `upsert(**fields)`. `compute_verdicts` calls `apply_fraction` only when `apply_result == "conflict"`.

---

## Execution Handoff

After saving, choose execution mode:
1. **Subagent-Driven (recommended)** — a fresh subagent per task, two-stage review between tasks.
2. **Inline Execution** — execute in this session with checkpoints.
