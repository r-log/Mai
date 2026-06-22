# Port-Verdict Phase 3 — Verdict Stage (the truthful MVP) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Combine the Phase-1 relevance class and the Phase-2 git-apply oracle into a per-(fix × core) **`PortVerdict`** — `needs | review | not_applicable | has_it` — where a confident **NEEDS** is produced *only* when git proves a clean apply AND every touched subsystem is portable-shared. This is the MVP: **the recommendations become truthful**, proven by validation gates (client-bound is never NEEDS even when it applies).

**Architecture:** A new `PortVerdict` model + repository; a pure `resolve_relevance` helper (touched subsystems → portable/divergent + magnitude); and `compute_verdicts(session, git_client)` that, for each fix and each non-present core, runs `paths_exist` → reverse `apply_check` → forward `apply_check`, grades against the relevance gate, and upserts a verdict (incrementally cached on `source_sha`+`base_sha`). Wired into `sync-analyze` alongside the existing `compute_port_candidates` (which keeps the current board working untouched — the board switches to verdicts in Phase 4).

**Tech Stack:** Python 3.12, async SQLAlchemy 2.0, pytest. All verdict logic is tested with `FakeGitClient` (scripted apply results); one real-git golden integration test.

## Global Constraints

- **Phase 3 of the Port-Verdict Engine** (`docs/specs/port-verdict-engine.md` §6.2–6.3, §7, §12, §13 P3). This is the **MVP boundary**: after it, trustworthy verdicts exist and are proven.
- **THE TRUTHFULNESS GATE (Invariant 1 & 2):** `verdict == "needs"` iff `apply_result == "clean"` **AND** every touched subsystem is classified `shared`. If a fix touches *any* `client_bound | expansion | vendored | mixed` subsystem, a clean apply yields **`review`**, never `needs`. Client-bound is never NEEDS.
- **Verdict grading** (per non-present core; see §6.3): no touched file exists → `not_applicable`; else reverse-applies → `has_it`; else forward clean → (`needs` if all-shared else `review`); else forward file_absent → `not_applicable`; else conflict → `review`.
- **Derived & recomputable:** `PortVerdict` is fully recomputed (no human status — that lives on `BoardItem`). Incremental: skip a `(fix, core)` whose existing verdict has the same `source_sha` and `base_sha`.
- **Non-breaking:** add `compute_verdicts` to the chain; **do not remove** `compute_port_candidates`/`PortCandidate` (the live board still reads them until Phase 4).
- 4-space indent. `feat:` commits, **NO AI attribution**. Commit with `git -c user.name="r-log" commit -m "..."`.
- Follow existing patterns: models in `src/mai/db/models.py`; repos in `src/mai/repository/`; sync stages in `src/mai/sync/`; `magnitude_tier` lives in `mai.repository.port_candidate`. `Propagation(patch_group_id, core, present, source_sha)`, `Commit(core, sha)`, `CommitFile(commit_id, subsystem, path, added_lines, removed_lines)`, `SubsystemClassRepository.get(subsystem)`. The `session` fixture + `FakeGitClient` drive tests. Run `python -m pytest`.

---

## File Structure

| File | Responsibility |
|------|----------------|
| `src/mai/db/models.py` (modify) | Add `PortVerdict`. |
| `src/mai/repository/port_verdict.py` (create) | `PortVerdictRepository`. |
| `src/mai/git/client.py` + `src/mai/git/fake.py` (modify) | Add `head_sha(core)` (protocol + Local + Fake). |
| `src/mai/sync/verdicts.py` (create) | `resolve_relevance` + `compute_verdicts`. |
| `src/mai/cli/__main__.py` (modify) | Run `compute_verdicts` in `sync-analyze`; report counts. |
| `tests/test_port_verdict_model.py` (create) | model + repo. |
| `tests/test_resolve_relevance.py` (create) | the relevance gate (pure). |
| `tests/test_compute_verdicts.py` (create) | the verdict logic + §12 validation gates + cache. |
| `tests/test_verdicts_integration.py` (create) | one real-git golden (shared fix → NEEDS). |

---

## Task 1: `PortVerdict` model + repository + `head_sha`

**Files:**
- Modify: `src/mai/db/models.py`
- Create: `src/mai/repository/port_verdict.py`
- Modify: `src/mai/git/client.py`, `src/mai/git/fake.py`
- Test: `tests/test_port_verdict_model.py`

**Interfaces:**
- Produces: `PortVerdict` (key `(patch_group_id, core)`; fields below); `PortVerdictRepository(session)` with `get(pg_id, core)`, `upsert(...)`, `actionable()`, `for_fix(pg_id)`; `GitClient.head_sha(core) -> str` (+ Local via `rev-parse HEAD`, + Fake via `head_shas` map / `f"head-{core}"` default).

- [ ] **Step 1: Write the failing test**

Create `tests/test_port_verdict_model.py`:

```python
from mai.repository.port_verdict import PortVerdictRepository


async def test_upsert_is_idempotent_and_overwrites(session):
    repo = PortVerdictRepository(session)
    await repo.upsert("pg1", "two", verdict="needs", apply_result="clean",
                      relevance="portable", source_core="three", source_sha="s1",
                      base_sha="b1", subsystem="src/shared", magnitude=4, tier="surgical",
                      confidence="high", similar_commit=None, evidence=["e"])
    await session.commit()
    # re-upsert same key with a new verdict -> overwrites (derived, no status to preserve)
    await repo.upsert("pg1", "two", verdict="review", apply_result="conflict",
                      relevance="portable", source_core="three", source_sha="s2",
                      base_sha="b2", subsystem="src/shared", magnitude=4, tier="surgical",
                      confidence="medium", similar_commit=None, evidence=["e2"])
    await session.commit()
    v = await repo.get("pg1", "two")
    assert v.verdict == "review" and v.source_sha == "s2" and v.base_sha == "b2"


async def test_actionable_and_for_fix(session):
    repo = PortVerdictRepository(session)
    for core, verdict in [("two", "needs"), ("one", "review"),
                          ("zero", "not_applicable"), ("four", "has_it")]:
        await repo.upsert("pg1", core, verdict=verdict, apply_result="clean",
                          relevance="portable", source_core="three", source_sha="s1",
                          base_sha="b1", subsystem="src/shared", magnitude=1, tier="surgical",
                          confidence="high", similar_commit=None, evidence=[])
    await session.commit()
    actionable = {(v.patch_group_id, v.core) for v in await repo.actionable()}
    assert actionable == {("pg1", "two"), ("pg1", "one")}     # needs + review only
    assert len(await repo.for_fix("pg1")) == 4
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_port_verdict_model.py -v`
Expected: FAIL (`ModuleNotFoundError: mai.repository.port_verdict`).

- [ ] **Step 3: Add the model**

In `src/mai/db/models.py`, add (existing imports cover `String, Integer, ForeignKey, UniqueConstraint, JSON, Mapped, mapped_column, _now, _uuid, datetime`):

```python
class PortVerdict(Base):
    """Derived per-(fix, core) verdict: does this core need this fix? Recomputable."""
    __tablename__ = "port_verdict"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    patch_group_id: Mapped[str] = mapped_column(ForeignKey("patch_group.id"))
    core: Mapped[str] = mapped_column(String(64))
    verdict: Mapped[str] = mapped_column(String(16))        # needs|review|not_applicable|has_it
    apply_result: Mapped[str] = mapped_column(String(16))   # clean|reverse_clean|conflict|file_absent
    relevance: Mapped[str] = mapped_column(String(16))      # portable|divergent
    source_core: Mapped[str] = mapped_column(String(64))
    source_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    base_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    subsystem: Mapped[str] = mapped_column(String(255))
    magnitude: Mapped[int] = mapped_column(Integer, default=0)
    tier: Mapped[str] = mapped_column(String(16), default="surgical")
    confidence: Mapped[str] = mapped_column(String(16), default="high")
    similar_commit: Mapped[str | None] = mapped_column(String(40), nullable=True)
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    computed_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)

    __table_args__ = (
        UniqueConstraint("patch_group_id", "core", name="uq_port_verdict"),
    )
```

- [ ] **Step 4: Write the repository**

Create `src/mai/repository/port_verdict.py`:

```python
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mai.db.models import PortVerdict


class PortVerdictRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get(self, pg_id: str, core: str) -> PortVerdict | None:
        return await self._session.scalar(
            select(PortVerdict).where(PortVerdict.patch_group_id == pg_id,
                                      PortVerdict.core == core))

    async def upsert(self, pg_id: str, core: str, **fields) -> PortVerdict:
        existing = await self.get(pg_id, core)
        if existing is not None:
            for k, v in fields.items():
                setattr(existing, k, v)
            return existing
        v = PortVerdict(patch_group_id=pg_id, core=core, **fields)
        self._session.add(v)
        return v

    async def actionable(self) -> list[PortVerdict]:
        return list(await self._session.scalars(
            select(PortVerdict).where(PortVerdict.verdict.in_(("needs", "review")))))

    async def for_fix(self, pg_id: str) -> list[PortVerdict]:
        return list(await self._session.scalars(
            select(PortVerdict).where(PortVerdict.patch_group_id == pg_id)))
```

- [ ] **Step 5: Add `head_sha` to the git client + protocol + fake**

In `src/mai/git/client.py`, add to the `GitClient` Protocol:

```python
    async def head_sha(self, core: str) -> str: ...
```

Add to `LocalGitClient`:

```python
    async def head_sha(self, core: str) -> str:
        return (await self._git(core, "rev-parse", "HEAD")).strip()
```

In `src/mai/git/fake.py`, extend `FakeGitClient.__init__` with a keyword `head_shas: dict[str, str] | None = None` (store `self._heads = head_shas or {}`) and add:

```python
    async def head_sha(self, core: str) -> str:
        return self._heads.get(core, f"head-{core}")
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_port_verdict_model.py -v`
Expected: PASS (2 passed).

- [ ] **Step 7: Commit**

```bash
git add src/mai/db/models.py src/mai/repository/port_verdict.py src/mai/git/client.py src/mai/git/fake.py tests/test_port_verdict_model.py
git -c user.name="r-log" commit -m "feat: PortVerdict model + repository + git head_sha"
```

---

## Task 2: `resolve_relevance` + `compute_verdicts` (+ validation gates)

**Files:**
- Create: `src/mai/sync/verdicts.py`
- Test: `tests/test_resolve_relevance.py`
- Test: `tests/test_compute_verdicts.py`

**Interfaces:**
- Consumes: `Propagation`, `Commit`, `CommitFile`, `SubsystemClassRepository`, `PortVerdictRepository`, `magnitude_tier`, the `GitClient` (`head_sha`/`diff`/`paths_exist`/`apply_check`).
- Produces: `resolve_relevance(touched, classes) -> tuple[str, int, str]` (relevance, magnitude, representative subsystem); `async compute_verdicts(session, git_client) -> dict` (counts `needs/review/not_applicable/has_it/cached/recomputed`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_resolve_relevance.py`:

```python
from mai.sync.verdicts import resolve_relevance


def _files(*subs):
    # (subsystem, added, removed) tuples standing in for CommitFile
    return [type("F", (), {"subsystem": s, "added_lines": a, "removed_lines": r})()
            for s, a, r in subs]


def test_all_shared_is_portable():
    files = _files(("src/shared/Database", 3, 1), ("src/shared/Log", 1, 0))
    classes = {"src/shared/Database": "shared", "src/shared/Log": "shared"}
    relevance, magnitude, rep = resolve_relevance(files, classes)
    assert relevance == "portable"
    assert magnitude == 5
    assert rep == "src/shared/Database"


def test_any_divergent_makes_it_divergent():
    # shared + client_bound in one patch -> NOT cleanly portable -> divergent
    files = _files(("src/shared/Database", 3, 1), ("src/game/Server/Opcodes", 9, 2))
    classes = {"src/shared/Database": "shared", "src/game/Server/Opcodes": "client_bound"}
    relevance, magnitude, rep = resolve_relevance(files, classes)
    assert relevance == "divergent"        # the client_bound touch bars portability


def test_expansion_and_mixed_are_divergent():
    files = _files(("src/game/Spells", 2, 0))
    assert resolve_relevance(files, {"src/game/Spells": "expansion"})[0] == "divergent"
    files = _files(("src/game/Maps", 2, 0))
    assert resolve_relevance(files, {"src/game/Maps": "mixed"})[0] == "divergent"
```

Create `tests/test_compute_verdicts.py`:

```python
from mai.db.models import (Commit, CommitFile, CommitPatch, PatchGroup,
                           Propagation, SubsystemClass)
from mai.git.fake import FakeGitClient
from mai.repository.port_verdict import PortVerdictRepository
from mai.sync.verdicts import compute_verdicts


async def _fix(session, *, pg_id, source_core, source_sha, subsystem, classification,
               present_cores, absent_cores, added=4):
    """A patch_group present in present_cores, absent in absent_cores, whose source
    commit touches one file in `subsystem` (classified `classification`)."""
    session.add(PatchGroup(id=pg_id, patch_id=f"patch-{pg_id}"))
    session.add(SubsystemClass(subsystem=subsystem, classification=classification,
                               source="heuristic"))
    for c in present_cores:
        session.add(Propagation(patch_group_id=pg_id, core=c, present=True,
                                source_sha=source_sha if c == source_core else f"{c}-sha"))
    for c in absent_cores:
        session.add(Propagation(patch_group_id=pg_id, core=c, present=False, source_sha=None))
    commit = Commit(core=source_core, sha=source_sha, author="a", authored_at="t",
                    committer="a", committed_at="t", message="fix", parent_shas=["p"],
                    is_merge=False)
    session.add(commit)
    await session.flush()
    session.add(CommitFile(commit_id=commit.id, path=f"{subsystem}/x.cpp",
                           change_type="M", added_lines=added, removed_lines=0,
                           subsystem=subsystem))
    await session.flush()


async def test_shared_clean_apply_is_NEEDS(session):
    await _fix(session, pg_id="pg1", source_core="three", source_sha="s1",
               subsystem="src/shared/Database", classification="shared",
               present_cores=["three"], absent_cores=["two"])
    await session.commit()
    fake = FakeGitClient(diffs={("three", "s1"): "PATCH1"},
                         paths={"two": ["src/shared/Database/x.cpp"]})
    # default forward apply -> clean
    await compute_verdicts(session, fake)
    v = await PortVerdictRepository(session).get("pg1", "two")
    assert v.verdict == "needs" and v.apply_result == "clean" and v.relevance == "portable"


async def test_client_bound_clean_apply_is_REVIEW_not_NEEDS(session):
    # THE TRUTHFULNESS GATE: applies cleanly, but the area is client-bound -> never NEEDS
    await _fix(session, pg_id="pg2", source_core="three", source_sha="s2",
               subsystem="src/game/Server/Opcodes", classification="client_bound",
               present_cores=["three"], absent_cores=["two"])
    await session.commit()
    fake = FakeGitClient(diffs={("three", "s2"): "PATCH2"},
                         paths={"two": ["src/game/Server/Opcodes/x.cpp"]})  # exists + applies clean
    await compute_verdicts(session, fake)
    v = await PortVerdictRepository(session).get("pg2", "two")
    assert v.apply_result == "clean"        # git said it merges
    assert v.verdict == "review"            # ...but the relevance gate held it back
    assert v.relevance == "divergent"


async def test_file_absent_is_NOT_APPLICABLE(session):
    await _fix(session, pg_id="pg3", source_core="four", source_sha="s3",
               subsystem="src/game/MoPThing", classification="mixed",
               present_cores=["four"], absent_cores=["zero"])
    await session.commit()
    fake = FakeGitClient(diffs={("four", "s3"): "PATCH3"}, paths={"zero": []})  # nothing exists
    await compute_verdicts(session, fake)
    v = await PortVerdictRepository(session).get("pg3", "zero")
    assert v.verdict == "not_applicable" and v.apply_result == "file_absent"


async def test_reverse_applies_is_HAS_IT(session):
    await _fix(session, pg_id="pg4", source_core="three", source_sha="s4",
               subsystem="src/shared/Log", classification="shared",
               present_cores=["three"], absent_cores=["two"])
    await session.commit()
    fake = FakeGitClient(
        diffs={("three", "s4"): "PATCH4"},
        paths={"two": ["src/shared/Log/x.cpp"]},
        apply_results={("two", "PATCH4", True): "reverse_clean"})  # already present
    await compute_verdicts(session, fake)
    v = await PortVerdictRepository(session).get("pg4", "two")
    assert v.verdict == "has_it" and v.apply_result == "reverse_clean"


async def test_conflict_is_REVIEW(session):
    await _fix(session, pg_id="pg5", source_core="three", source_sha="s5",
               subsystem="src/shared/Auth", classification="shared",
               present_cores=["three"], absent_cores=["two"])
    await session.commit()
    fake = FakeGitClient(
        diffs={("three", "s5"): "PATCH5"},
        paths={"two": ["src/shared/Auth/x.cpp"]},
        apply_results={("two", "PATCH5", False): "conflict"})
    await compute_verdicts(session, fake)
    v = await PortVerdictRepository(session).get("pg5", "two")
    assert v.verdict == "review" and v.apply_result == "conflict"


async def test_incremental_cache_skips_unchanged(session):
    await _fix(session, pg_id="pg6", source_core="three", source_sha="s6",
               subsystem="src/shared/Db", classification="shared",
               present_cores=["three"], absent_cores=["two"])
    await session.commit()
    fake = FakeGitClient(diffs={("three", "s6"): "P6"},
                         paths={"two": ["src/shared/Db/x.cpp"]},
                         head_shas={"two": "headTWO"})
    first = await compute_verdicts(session, fake)
    second = await compute_verdicts(session, fake)   # nothing changed
    assert first["recomputed"] >= 1
    assert second["cached"] >= 1 and second["recomputed"] == 0
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_resolve_relevance.py tests/test_compute_verdicts.py -v`
Expected: FAIL (`ModuleNotFoundError: mai.sync.verdicts`).

- [ ] **Step 3: Implement `verdicts.py`**

Create `src/mai/sync/verdicts.py`:

```python
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mai.db.models import Commit, CommitFile, PatchGroup, Propagation
from mai.repository.port_candidate import magnitude_tier
from mai.repository.port_verdict import PortVerdictRepository
from mai.repository.subsystem_class import SubsystemClassRepository


def resolve_relevance(files, classes: dict[str, str]) -> tuple[str, int, str]:
    """Resolve a fix's portability from its touched files + subsystem classes.

    portable iff EVERY touched subsystem is classified 'shared' (a patch that also
    touches client_bound/expansion/vendored/mixed code cannot be a clean cross-port).
    magnitude = portable (shared) lines when portable, else all touched lines.
    Returns (relevance, magnitude, representative_subsystem).
    """
    touched = sorted({f.subsystem for f in files})
    all_shared = bool(touched) and all(classes.get(s) == "shared" for s in touched)
    if all_shared:
        magnitude = sum(f.added_lines + f.removed_lines for f in files)
        return "portable", magnitude, touched[0]
    magnitude = sum(f.added_lines + f.removed_lines for f in files)
    return "divergent", magnitude, (touched[0] if touched else "(root)")


async def compute_verdicts(session: AsyncSession, git_client) -> dict:
    """For each fix x each non-present core, grade applicability x relevance into a
    PortVerdict. NEEDS only when the patch applies cleanly AND every touched
    subsystem is shared. Incremental: cached on (source_sha, base_sha). Offline DB +
    git worktrees; no network."""
    rows = (await session.execute(
        select(PatchGroup.id, Propagation.core, Propagation.present,
               Propagation.source_sha))).all()
    groups: dict[str, dict[str, list]] = defaultdict(
        lambda: {"present": [], "absent": []})
    for r in rows:
        groups[r.id]["present" if r.present else "absent"].append((r.core, r.source_sha))

    vrepo = PortVerdictRepository(session)
    sc_repo = SubsystemClassRepository(session)
    counts = {"needs": 0, "review": 0, "not_applicable": 0, "has_it": 0,
              "cached": 0, "recomputed": 0}
    head_cache: dict[str, str] = {}

    for pg_id, gd in groups.items():
        if not gd["present"] or not gd["absent"]:
            continue
        source_core, source_sha = min(gd["present"], key=lambda t: t[0])
        if source_sha is None:
            continue
        commit = await session.scalar(
            select(Commit).where(Commit.core == source_core, Commit.sha == source_sha))
        if commit is None:
            continue
        files = list(await session.scalars(
            select(CommitFile).where(CommitFile.commit_id == commit.id)))
        if not files:
            continue
        classes = {f.subsystem: (await sc_repo.get(f.subsystem)) for f in files}
        classes = {s: (c.classification if c else "mixed") for s, c in classes.items()}
        relevance, magnitude, rep = resolve_relevance(files, classes)
        paths = sorted({f.path for f in files})
        patch: str | None = None

        for target_core, _ in gd["absent"]:
            if target_core not in head_cache:
                head_cache[target_core] = await git_client.head_sha(target_core)
            base = head_cache[target_core]
            existing = await vrepo.get(pg_id, target_core)
            if (existing is not None and existing.source_sha == source_sha
                    and existing.base_sha == base):
                counts["cached"] += 1
                counts[existing.verdict] = counts.get(existing.verdict, 0) + 1
                continue

            if patch is None:
                patch = await git_client.diff(source_core, source_sha)
            exists = await git_client.paths_exist(target_core, paths)
            if not any(exists.values()):
                verdict, apply_result = "not_applicable", "file_absent"
            elif await git_client.apply_check(target_core, patch, reverse=True) == "reverse_clean":
                verdict, apply_result = "has_it", "reverse_clean"
            else:
                apply_result = await git_client.apply_check(target_core, patch)
                if apply_result == "clean":
                    verdict = "needs" if relevance == "portable" else "review"
                elif apply_result == "file_absent":
                    verdict = "not_applicable"
                else:
                    verdict = "review"

            confidence = "high" if verdict in ("needs", "has_it") else "medium"
            evidence = [f"source {source_core}@{source_sha}",
                        f"apply {apply_result}", f"relevance {relevance} ({rep})",
                        f"absent-by-patch-id in {target_core}"]
            await vrepo.upsert(
                pg_id, target_core, verdict=verdict, apply_result=apply_result,
                relevance=relevance, source_core=source_core, source_sha=source_sha,
                base_sha=base, subsystem=rep, magnitude=magnitude,
                tier=magnitude_tier(magnitude), confidence=confidence,
                similar_commit=None, evidence=evidence)
            counts["recomputed"] += 1
            counts[verdict] = counts.get(verdict, 0) + 1

    await session.commit()
    return counts
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_resolve_relevance.py tests/test_compute_verdicts.py -v`
Expected: PASS (3 + 6 = 9 passed). The headline gate test `test_client_bound_clean_apply_is_REVIEW_not_NEEDS` proves apply-clean + client-bound → REVIEW.

- [ ] **Step 5: Commit**

```bash
git add src/mai/sync/verdicts.py tests/test_resolve_relevance.py tests/test_compute_verdicts.py
git -c user.name="r-log" commit -m "feat: compute_verdicts — applicability x relevance, truthful NEEDS gate"
```

---

## Task 3: Wire into sync-analyze + real-git golden

**Files:**
- Modify: `src/mai/cli/__main__.py`
- Test: `tests/test_verdicts_integration.py`

**Interfaces:**
- Consumes: `compute_verdicts` (Task 2), `LocalGitClient` (Phase 2).
- Produces: `sync-analyze` runs `compute_verdicts` after `compute_port_candidates` (additive); CLI prints verdict counts. A real-git golden integration test.

- [ ] **Step 1: Write the failing integration test**

Create `tests/test_verdicts_integration.py`:

```python
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
    (sub / "log.cpp").write_text("a\nb\n")
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_verdicts_integration.py -v`
Expected: FAIL until `compute_verdicts` is importable (it is, from Task 2) — so this should actually PASS once Task 2 is merged; if it fails, read the failure (a real apply/grade bug) and fix the minimal cause. (This test is the end-to-end proof that the Fake-tested logic also holds on real git.)

- [ ] **Step 3: Wire `compute_verdicts` into `sync-analyze`**

In `src/mai/cli/__main__.py`, `_sync_analyze` currently runs propagation/classify/portcandidates. Add the verdict stage (it needs a git client). Change `_sync_analyze` to build a `LocalGitClient` and run `compute_verdicts`:

```python
async def _sync_analyze() -> dict:
    from mai.git.client import LocalGitClient
    from mai.sync.classify import classify_subsystems
    from mai.sync.portcandidates import compute_port_candidates
    from mai.sync.propagate import compute_propagation
    from mai.sync.verdicts import compute_verdicts

    async with SessionFactory() as session:
        propagation = await compute_propagation(session)
        classification = await classify_subsystems(session)
        port_candidates = await compute_port_candidates(session)
        verdicts = await compute_verdicts(session, LocalGitClient(settings.git_mirror_dir))
        return {"propagation": propagation, "classification": classification,
                "port_candidates": port_candidates, "verdicts": verdicts}
```

Then in `main()`'s `sync-analyze` dispatch, append a verdict line to the print:

```python
        v = result["verdicts"]
        print(f"verdicts: needs={v['needs']} review={v['review']} "
              f"n/a={v['not_applicable']} has_it={v['has_it']} "
              f"(recomputed={v['recomputed']} cached={v['cached']})")
```

- [ ] **Step 4: Run tests + full suite**

Run: `python -m pytest tests/test_verdicts_integration.py -v`
Expected: PASS.
Run: `python -m pytest -q`
Expected: full suite green. (`compute_port_candidates`/`PortCandidate` are untouched — the live board still works; verdicts are computed in parallel.)

- [ ] **Step 5: Commit**

```bash
git add src/mai/cli/__main__.py tests/test_verdicts_integration.py
git -c user.name="r-log" commit -m "feat: run compute_verdicts in sync-analyze + real-git golden"
```

---

## Self-Review

**Spec coverage (`port-verdict-engine.md` Phase 3, §6.2–6.3, §12):**
- `PortVerdict` model + repo → Task 1; `head_sha` (base_sha capture, the P2-handoff note) → Task 1.
- Verdict grading (reverse→has_it / clean→needs|review / file_absent→n_a / conflict→review) → Task 2 `compute_verdicts`.
- **Truthfulness gate (Invariant 1 & 2):** NEEDS iff clean AND all-shared; client_bound clean→REVIEW → Task 2 + `test_client_bound_clean_apply_is_REVIEW_not_NEEDS` (the §12.2 gate, non-vacuous: asserts apply was clean yet verdict is review).
- §12 gates 1–6 → Task 2 tests (shared→needs, client-bound→review, absent→n/a, reverse→has_it, conflict→review, determinism/cache) + Task 3 real-git golden (§12.7).
- Incremental cache on (source_sha, base_sha) → Task 2.
- Non-breaking: `compute_port_candidates` kept; board untouched → Task 3 (additive).

**Deliberately deferred (not gaps):** `similar_commit` detection (left `None`; spec §14 #5 open) — the "maybe already fixed differently" hint is a follow-on; conflicts still correctly land in REVIEW without it. The board re-model that consumes `PortVerdict` is Phase 4. Per-file partial-NEEDS (a patch's shared files graduating while its divergent files don't) is a future refinement — Phase 3 uses the conservative whole-patch gate (any divergent touch → REVIEW), which is strictly truthful.

**Placeholder scan:** none — complete code throughout; the integration test's `_Client` wrapper is a real, working subclass (the source repo's diff is supplied deterministically).

**Type consistency:** `compute_verdicts(session, git_client) -> dict`, `resolve_relevance(files, classes) -> (str,int,str)`, `PortVerdictRepository.upsert(pg_id, core, **fields)`, `head_sha(core) -> str` are used identically across tasks; verdict strings `needs|review|not_applicable|has_it` and apply strings `clean|reverse_clean|conflict|file_absent` match the model + the Phase-2 `apply_check` vocabulary.

---

## Execution Handoff

After saving, choose execution mode:
1. **Subagent-Driven (recommended)** — a fresh subagent per task, two-stage review between tasks.
2. **Inline Execution** — execute in this session with checkpoints.
