# Mai Sync Engine — Phase 2a: Patch Propagation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the raw commit/patch-id data (Phase 1) into the **"which forks have this fix" matrix** — group commits across forks by `git patch-id` into canonical fixes (`PatchGroup`), and record per-fork `Propagation` (present/absent + how we know), augmented by `(cherry picked from commit …)` trailer detection so a port whose diff drifted is still recognized.

**Architecture:** A pure cherry-trailer parser (`sync/cherry.py`) + two derived tables (`PatchGroup`, `Propagation`) behind repositories + one offline compute pass (`sync/propagate.py`) that reads stored `Commit`/`CommitPatch` rows, builds an in-memory present/absent matrix keyed by `(patch_id, core)`, augments it from cherry-trailers, and persists it idempotently. No git, no network — it operates on Phase 1's DB rows. CLI `sync-analyze` runs it.

**Tech Stack:** Python 3.12 · SQLAlchemy 2.0 async · `re` (stdlib) · pytest + pytest-asyncio. No new dependency.

## Global Constraints

Copied verbatim from `docs/specs/sync-intelligence-engine.md`:
- **Proven over inferred, and always labeled.** Each propagation determination records `via` (how we know) and `evidence`. Phase 2a uses only the strong signals (`patch_id`, `cherry_trailer`) → `confidence="high"`; the `medium`/`inferred` tier (squash/similarity) is Phase 2c, not here.
- **Raw is append-only; everything else recomputable.** `PatchGroup`/`Propagation` are derived: the compute pass is re-runnable and rebuilds them in place (idempotent upsert).
- **Git vouches.** Identity is git's `patch-id` (captured in Phase 1's `CommitPatch.patch_id`); we never re-hash diffs here.
- **Install-target-agnostic.** The fork universe is derived from harvested data (`distinct Commit.core`), never hard-coded.
- **Match the stack:** async SQLAlchemy 2.0, repository seam for all DB access, 4-space indent, `feat:`-style commit messages, **no AI attribution in commits**.

---

## Builds on existing code

These already exist and MUST be reused as-is (do not redefine):
- `mai.db.models` — `Commit(core, sha, author, authored_at, committer, committed_at, message, parent_shas, is_merge)`, `CommitPatch(commit_id, patch_id, normalized_hash, aggregate_of)`. Helpers `_uuid`, `_now`; imports `String/Text/Integer/Boolean/JSON/ForeignKey/UniqueConstraint/Mapped/mapped_column/datetime` already present at the top of `models.py`.
- `tests/conftest.py` — the async in-memory sqlite `session` fixture (`Base.metadata.create_all`; test modules import the new models so they register).
- The derived-compute pattern of `mai.correlate.run.correlate_all` (compute → `session.commit()` → return a counts dict) and the upsert pattern of `mai.repository.drift.DriftRepository`.
- CLI pattern in `src/mai/cli/__main__.py` (subparser + `async def _cmd()` + dispatch); `_correlate` is the closest precedent (offline, no external key).

## File Structure

```
src/mai/
  db/models.py                   # MODIFY: add PatchGroup, Propagation
  sync/
    __init__.py                  # new (empty)
    cherry.py                    # parse_cherry_sources (pure regex)
    propagate.py                 # compute_propagation (offline derived pass)
  repository/propagation.py      # PatchGroupRepository + PropagationRepository (seam)
  cli/__main__.py                # MODIFY: add sync-analyze subcommand
tests/
  test_cherry.py
  test_propagation_repository.py
  test_propagate.py
```

---

### Task 1: Cherry-trailer parser

**Files:**
- Create: `mai/src/mai/sync/__init__.py`
- Create: `mai/src/mai/sync/cherry.py`
- Create: `mai/tests/test_cherry.py`

**Interfaces:**
- Produces: `parse_cherry_sources(message: str) -> list[str]` — the deduped, order-preserving list of source SHAs cited by `(cherry picked from commit <sha>)` trailers.

- [ ] **Step 1: Create `sync/__init__.py` (empty marker)**

```python
```

- [ ] **Step 2: Write the failing test**

`mai/tests/test_cherry.py`:

```python
from mai.sync.cherry import parse_cherry_sources


def test_parses_single_cherry_trailer():
    msg = "Fix pet threat\n\n(cherry picked from commit a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0)"
    assert parse_cherry_sources(msg) == ["a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0"]


def test_parses_multiple_and_dedupes_preserving_order():
    msg = ("port\n\n(cherry picked from commit aaaaaaa)\n"
           "(cherry picked from commit bbbbbbb)\n"
           "(cherry picked from commit aaaaaaa)")
    assert parse_cherry_sources(msg) == ["aaaaaaa", "bbbbbbb"]


def test_case_insensitive_and_short_sha():
    assert parse_cherry_sources("x\n(Cherry picked from commit ABC1234)") == ["ABC1234"]


def test_no_trailer_returns_empty():
    assert parse_cherry_sources("just a normal commit message") == []


def test_none_or_empty_message_is_safe():
    assert parse_cherry_sources("") == []
    assert parse_cherry_sources(None) == []
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /c/Users/Roko/Documents/PYTHON/MANGOS/mai && python -m pytest tests/test_cherry.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mai.sync.cherry'`
(Use `py -3.12 -m pytest ...` if `python` is not the 3.12 interpreter.)

- [ ] **Step 4: Write `sync/cherry.py`**

```python
import re

# Matches git's standard backport trailer: "(cherry picked from commit <hex>)".
_CHERRY = re.compile(r"cherry picked from commit ([0-9a-f]{7,40})", re.IGNORECASE)


def parse_cherry_sources(message: str) -> list[str]:
    """Return the source SHAs cited by cherry-pick trailers, deduped, in first-seen order."""
    if not message:
        return []
    return list(dict.fromkeys(_CHERRY.findall(message)))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /c/Users/Roko/Documents/PYTHON/MANGOS/mai && python -m pytest tests/test_cherry.py -v`
Expected: PASS (5 passed)

- [ ] **Step 6: Commit**

```bash
git add mai/src/mai/sync/__init__.py mai/src/mai/sync/cherry.py mai/tests/test_cherry.py
git commit -m "feat: cherry-pick trailer parser (parse_cherry_sources)"
```

---

### Task 2: `PatchGroup` + `Propagation` models + repositories

**Files:**
- Modify: `mai/src/mai/db/models.py` (append two classes)
- Create: `mai/src/mai/repository/propagation.py`
- Create: `mai/tests/test_propagation_repository.py`

**Interfaces:**
- Produces: ORM `PatchGroup(patch_id)`, `Propagation(patch_group_id, core, present, via, confidence, source_sha, evidence)`;
  `PatchGroupRepository(session).get_or_create(patch_id) -> PatchGroup`;
  `PropagationRepository(session).upsert(patch_group_id, core, *, present, via, confidence, source_sha, evidence) -> None`.

- [ ] **Step 1: Append the two models to `db/models.py`**

Append at the END of `mai/src/mai/db/models.py` (all needed imports already exist there):

```python
class PatchGroup(Base):
    """Derived: a canonical fix identity, keyed by git patch-id; members span forks."""
    __tablename__ = "patch_group"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    patch_id: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(default=_now)


class Propagation(Base):
    """Derived: whether a fix (patch_group) is present in a core, and how we know."""
    __tablename__ = "propagation"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    patch_group_id: Mapped[str] = mapped_column(ForeignKey("patch_group.id"))
    core: Mapped[str] = mapped_column(String(64))
    present: Mapped[bool] = mapped_column(Boolean, default=False)
    via: Mapped[str | None] = mapped_column(String(40), nullable=True)   # patch_id | cherry_trailer | "a+b"
    confidence: Mapped[str] = mapped_column(String(16), default="high")  # high | medium
    source_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)

    __table_args__ = (
        UniqueConstraint("patch_group_id", "core", name="uq_propagation"),
    )
```

- [ ] **Step 2: Write the failing test**

`mai/tests/test_propagation_repository.py`:

```python
from sqlalchemy import func, select

from mai.db.models import PatchGroup, Propagation
from mai.repository.propagation import PatchGroupRepository, PropagationRepository


async def test_get_or_create_is_idempotent(session):
    repo = PatchGroupRepository(session)
    a = await repo.get_or_create("pid-1")
    await session.flush()
    b = await repo.get_or_create("pid-1")
    assert a.id == b.id
    assert await session.scalar(select(func.count()).select_from(PatchGroup)) == 1


async def test_upsert_inserts_then_updates_single_row(session):
    pg = await PatchGroupRepository(session).get_or_create("pid-1")
    await session.flush()
    prop = PropagationRepository(session)
    await prop.upsert(pg.id, "three", present=False, via=None,
                      confidence="high", source_sha=None, evidence=[])
    await prop.upsert(pg.id, "three", present=True, via="patch_id",
                      confidence="high", source_sha="abc", evidence=["e1"])
    await session.commit()
    assert await session.scalar(select(func.count()).select_from(Propagation)) == 1
    row = await session.scalar(select(Propagation))
    assert row.present is True and row.via == "patch_id"
    assert row.source_sha == "abc" and row.evidence == ["e1"]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /c/Users/Roko/Documents/PYTHON/MANGOS/mai && python -m pytest tests/test_propagation_repository.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mai.repository.propagation'`

- [ ] **Step 4: Write `repository/propagation.py`**

```python
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mai.db.models import PatchGroup, Propagation


class PatchGroupRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_or_create(self, patch_id: str) -> PatchGroup:
        existing = await self._session.scalar(
            select(PatchGroup).where(PatchGroup.patch_id == patch_id)
        )
        if existing:
            return existing
        pg = PatchGroup(patch_id=patch_id)
        self._session.add(pg)
        await self._session.flush()  # populate pg.id
        return pg


class PropagationRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def upsert(self, patch_group_id: str, core: str, *, present: bool,
                     via: str | None, confidence: str, source_sha: str | None,
                     evidence: list) -> None:
        existing = await self._session.scalar(
            select(Propagation).where(
                Propagation.patch_group_id == patch_group_id,
                Propagation.core == core,
            )
        )
        if existing:
            existing.present = present
            existing.via = via
            existing.confidence = confidence
            existing.source_sha = source_sha
            existing.evidence = evidence
        else:
            self._session.add(Propagation(
                patch_group_id=patch_group_id, core=core, present=present, via=via,
                confidence=confidence, source_sha=source_sha, evidence=evidence,
            ))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /c/Users/Roko/Documents/PYTHON/MANGOS/mai && python -m pytest tests/test_propagation_repository.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add mai/src/mai/db/models.py mai/src/mai/repository/propagation.py mai/tests/test_propagation_repository.py
git commit -m "feat: PatchGroup/Propagation models + repositories (get_or_create, upsert)"
```

---

### Task 3: `compute_propagation` derived pass

**Files:**
- Create: `mai/src/mai/sync/propagate.py`
- Create: `mai/tests/test_propagate.py`

**Interfaces:**
- Consumes: `Commit`, `CommitPatch` (Phase 1); `PatchGroupRepository`, `PropagationRepository` (Task 2); `parse_cherry_sources` (Task 1).
- Produces: `compute_propagation(session) -> dict` with keys `groups`, `present`, `absent`, `cherry_links`.

- [ ] **Step 1: Write the failing test**

`mai/tests/test_propagate.py`:

```python
from sqlalchemy import func, select

from mai.db.models import CommitPatch, PatchGroup, Propagation
from mai.db.models import Commit
from mai.sync.propagate import compute_propagation


async def _add(session, core, sha, patch_id, message="m"):
    c = Commit(core=core, sha=sha, author="a", authored_at="t", committer="a",
               committed_at="t", message=message, parent_shas=["p"], is_merge=False)
    session.add(c)
    await session.flush()
    session.add(CommitPatch(commit_id=c.id, patch_id=patch_id))
    await session.flush()
    return c


async def _prop(session, patch_id, core):
    return await session.scalar(
        select(Propagation).join(PatchGroup,
                                 PatchGroup.id == Propagation.patch_group_id)
        .where(PatchGroup.patch_id == patch_id, Propagation.core == core)
    )


async def test_present_and_absent_matrix(session):
    await _add(session, "three", "s_three", "P1")
    await _add(session, "two", "s_two", "P1")
    await _add(session, "zero", "s_zero", "P9")
    await session.commit()

    result = await compute_propagation(session)
    assert result["groups"] == 2          # P1, P9
    # P1 present in three+two, absent in zero
    assert (await _prop(session, "P1", "three")).present is True
    assert (await _prop(session, "P1", "two")).present is True
    assert (await _prop(session, "P1", "zero")).present is False
    # P9 present only in zero
    assert (await _prop(session, "P9", "zero")).present is True
    assert (await _prop(session, "P9", "three")).present is False
    # 2 groups x 3 cores = 6 rows; 3 present (P1:two,three; P9:zero), 3 absent
    assert result["present"] == 3 and result["absent"] == 3


async def test_cherry_trailer_links_despite_patch_mismatch(session):
    a = "a" * 40
    await _add(session, "three", a, "P1")
    # two has a DIFFERENT patch-id but cites three's commit as the cherry source
    await _add(session, "two", "b" * 40, "P2",
               message=f"port fix\n\n(cherry picked from commit {a})")
    await session.commit()

    result = await compute_propagation(session)
    p1_two = await _prop(session, "P1", "two")
    assert p1_two.present is True
    assert "cherry_trailer" in p1_two.via
    assert result["cherry_links"] == 1


async def test_recompute_is_idempotent(session):
    await _add(session, "three", "s1", "P1")
    await _add(session, "two", "s2", "P1")
    await session.commit()
    await compute_propagation(session)
    await compute_propagation(session)
    assert await session.scalar(select(func.count()).select_from(PatchGroup)) == 1
    assert await session.scalar(select(func.count()).select_from(Propagation)) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/Users/Roko/Documents/PYTHON/MANGOS/mai && python -m pytest tests/test_propagate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mai.sync.propagate'`

- [ ] **Step 3: Write `sync/propagate.py`**

```python
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mai.db.models import Commit, CommitPatch
from mai.repository.propagation import PatchGroupRepository, PropagationRepository
from mai.sync.cherry import parse_cherry_sources


async def compute_propagation(session: AsyncSession) -> dict:
    """Group commits across forks by patch-id and record per-fork presence.

    Reads stored Commit/CommitPatch rows (offline), builds a present/absent matrix
    keyed by (patch_id, core), augments it with cherry-pick-trailer links, and
    persists PatchGroup + Propagation idempotently. Recomputable.
    """
    rows = (await session.execute(
        select(Commit.core, Commit.sha, CommitPatch.patch_id, Commit.message)
        .join(CommitPatch, CommitPatch.commit_id == Commit.id)
        .where(CommitPatch.patch_id.is_not(None))
    )).all()

    tracked = sorted({r.core for r in rows})
    sha_to_patch = {r.sha: r.patch_id for r in rows}

    # patch_id -> {core: first sha seen}
    present_by_patch: dict[str, dict[str, str]] = defaultdict(dict)
    for r in rows:
        present_by_patch[r.patch_id].setdefault(r.core, r.sha)

    patch_ids = sorted(present_by_patch)
    # matrix[(patch_id, core)] = {present, vias:set, sha, evidence:list}
    matrix: dict[tuple[str, str], dict] = {}
    for pid in patch_ids:
        for core in tracked:
            if core in present_by_patch[pid]:
                sha = present_by_patch[pid][core]
                matrix[(pid, core)] = {"present": True, "vias": {"patch_id"},
                                       "sha": sha,
                                       "evidence": [f"patch_id {pid} in {core}@{sha}"]}
            else:
                matrix[(pid, core)] = {"present": False, "vias": set(),
                                       "sha": None, "evidence": []}

    cherry_links = 0
    for r in rows:
        for src in parse_cherry_sources(r.message):
            src_pid = sha_to_patch.get(src)
            if src_pid is None:
                continue
            cell = matrix.get((src_pid, r.core))
            if cell is None:
                continue
            if not cell["present"]:
                cell["present"] = True
                cell["sha"] = r.sha
            cell["vias"].add("cherry_trailer")
            cell["evidence"].append(f"cherry-trail {r.core}@{r.sha} <- {src}")
            cherry_links += 1

    pg_repo = PatchGroupRepository(session)
    prop_repo = PropagationRepository(session)
    n_present = n_absent = 0
    for pid in patch_ids:
        pg = await pg_repo.get_or_create(pid)
        for core in tracked:
            cell = matrix[(pid, core)]
            via = "+".join(sorted(cell["vias"])) if cell["vias"] else None
            await prop_repo.upsert(pg.id, core, present=cell["present"], via=via,
                                   confidence="high", source_sha=cell["sha"],
                                   evidence=cell["evidence"])
            if cell["present"]:
                n_present += 1
            else:
                n_absent += 1

    await session.commit()
    return {"groups": len(patch_ids), "present": n_present,
            "absent": n_absent, "cherry_links": cherry_links}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /c/Users/Roko/Documents/PYTHON/MANGOS/mai && python -m pytest tests/test_propagate.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add mai/src/mai/sync/propagate.py mai/tests/test_propagate.py
git commit -m "feat: compute_propagation — cross-fork patch-id matrix + cherry-trailer augmentation"
```

---

### Task 4: CLI `sync-analyze` + full-suite green + offline smoke

**Files:**
- Modify: `mai/src/mai/cli/__main__.py` (add subcommand)

**Interfaces:**
- Consumes: `compute_propagation` (Task 3); `SessionFactory` (existing).

- [ ] **Step 1: Add the `_sync_analyze` coroutine to `cli/__main__.py`**

Add after the existing `_commits_harvest` coroutine in `mai/src/mai/cli/__main__.py`:

```python
async def _sync_analyze() -> dict:
    from mai.sync.propagate import compute_propagation

    async with SessionFactory() as session:
        return await compute_propagation(session)
```

- [ ] **Step 2: Register + dispatch the subcommand in `main()`**

In `main()`, add the parser after `sub.add_parser("commits-harvest")`:

```python
    sub.add_parser("sync-analyze")
```

And add the dispatch branch after the `commits-harvest` branch:

```python
    elif args.cmd == "sync-analyze":
        result = asyncio.run(_sync_analyze())
        print(f"sync-analyze: groups={result['groups']} present={result['present']} "
              f"absent={result['absent']} cherry_links={result['cherry_links']}")
```

- [ ] **Step 3: Run the full test suite**

Run: `cd /c/Users/Roko/Documents/PYTHON/MANGOS/mai && python -m pytest -q`
Expected: PASS — the prior suite (121) plus this plan's new tests (5 + 2 + 3 = 10), so **131 passed** (the 3 `LocalGitClient` tests pass since git is present on this box).

- [ ] **Step 4: Offline smoke — propagation over a fixtured DB**

Write `mai-data/tmp/smoke_propagate.py`:

```python
import asyncio

from mai.db.base import Base
from mai.db.models import Commit, CommitPatch
from mai.db.session import SessionFactory, engine
from mai.sync.propagate import compute_propagation


async def main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionFactory() as s:
        for core, sha, pid in [("three", "x1", "PID"), ("two", "x2", "PID"),
                               ("zero", "x3", "OTHER")]:
            c = Commit(core=core, sha=sha, author="a", authored_at="t", committer="a",
                       committed_at="t", message="m", parent_shas=["p"], is_merge=False)
            s.add(c)
            await s.flush()
            s.add(CommitPatch(commit_id=c.id, patch_id=pid))
        await s.commit()
        print(await compute_propagation(s))


asyncio.run(main())
```

Run: `cd /c/Users/Roko/Documents/PYTHON/MANGOS/mai && rm -f mai.db && python mai-data/tmp/smoke_propagate.py`
Expected: prints `{'groups': 2, 'present': 3, 'absent': 3, 'cherry_links': 0}` (PID present in three+two/absent in zero; OTHER present in zero/absent in the other two). Then `rm -f mai.db` to leave no artifact.

- [ ] **Step 5: Commit**

```bash
git add mai/src/mai/cli/__main__.py
git commit -m "feat: mai CLI sync-analyze subcommand (runs propagation pass)"
```

---

## Self-Review

- **Spec coverage (Phase 2 — propagation slice of `sync-intelligence-engine.md` §6.2/§13):** `PatchGroup` (canonical fix identity by patch-id) ✓; `Propagation` (per-core present/absent + `via` + `confidence` + `evidence`) ✓; patch-id grouping + `git cherry`-trailer detection (the `(cherry picked from commit …)` signal — §11.5) ✓; CLI surface ✓. **Out of scope by design (later 2b/2c):** `SubsystemClass`, `PortCandidate`, PR-aggregate squash-match (`CommitPatch.aggregate_of` stays null), the `inferred`/`medium`-confidence tier, and the real-fork live golden-cherry-pick (§12.1 at integration scale — needs real harvested commits from r-log forks, gated to Phase 4/5). The §12.1 cherry-detection logic IS proven here deterministically at unit level (`test_cherry_trailer_links_despite_patch_mismatch`).
- **Invariants:** proven+labeled (`via`/`evidence`, all `high` this phase) ✓ · recomputable derived tables (idempotent upsert / get_or_create) ✓ · git-vouched identity (reuses Phase 1 `patch_id`) ✓ · install-target-agnostic (universe = `distinct Commit.core`) ✓ · append-only raw untouched ✓.
- **Placeholder scan:** none — every step has runnable code/commands with expected output.
- **Type consistency:** `parse_cherry_sources(message) -> list[str]` used identically in `propagate.py` and tests; `PatchGroupRepository.get_or_create(patch_id) -> PatchGroup` and `PropagationRepository.upsert(patch_group_id, core, *, present, via, confidence, source_sha, evidence)` signatures match between Task 2 definition and Task 3 calls; `compute_propagation(session) -> dict` keys (`groups/present/absent/cherry_links`) match the CLI print in Task 4.

## Notes for later plans (2b, 2c)

- **Phase 2b (`15-...`): `SubsystemClass`** — classify each subsystem `shared | expansion | mixed` (seed from `CLAUDE.md`: `src/shared/**`, `dep/**`, `src/game/Server/**` = shared; spell/talent/quest/raid + DBC-bound = expansion; mixed resolved at file granularity). Pure path classifier + seed table + manual-override support.
- **Phase 2c (`16-...`): `PortCandidate`** — join `Propagation` (absent rows) × `SubsystemClass` (shared) × `CommitFile` (touched files) → port-debt candidates with `confidence` + `evidence`; add the PR-aggregate squash-match fallback (`CommitPatch.aggregate_of`, `via="squash_match"`, `medium` confidence) and the file+line-range similarity `inferred` tier; extend `sync-analyze` to run the full chain; wire the §12 validation gates (design-divergence anchor, L1 cross-check) and the live golden-cherry-pick once r-log forks are harvested. **← MVP boundary: "the data is correct."**
- **Native vs cherry origin:** Phase 2a records *that* a fork has a fix (present) and *how detected* (`via`), not which fork was the origin. Origin/direction inference (earliest authored_at across members) is a 2c refinement if PortCandidate needs a default port-source.
- **Migrations:** new tables still rely on `Base.metadata.create_all`; the Postgres/Neon deploy plan introduces Alembic + a baseline.
