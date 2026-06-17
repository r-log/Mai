# Mai Sync Engine — Phase 2c: Port Candidates (MVP) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce the **trustworthy port-debt backlog** — a `PortCandidate` for every fix that is *present in one fork, absent in another, and touches a shared (portable) subsystem* — each carrying confidence + evidence, plus the §12 validation gates that prove the engine never flags by-design (expansion) divergence. This completes the MVP: **"the data is correct."**

**Architecture:** A `PortCandidate` derived table behind a repository whose upsert **preserves human `status`** (open/ported/dismissed). One offline synthesis pass (`compute_port_candidates`) joins the Phase-2a `Propagation` matrix (present/absent per fork) with the Phase-2b `SubsystemClass` (shared/expansion/mixed) and the Phase-1 `CommitFile` data (touched subsystems + line magnitude): for each fix with ≥1 absent fork whose source commit touches a `shared` subsystem, it emits one candidate per absent target, and **auto-resolves** candidates whose target later acquires the fix. An explicit validation-gates test suite proves §12 properties. `sync-analyze` runs it as the final stage.

**Tech Stack:** Python 3.12 · SQLAlchemy 2.0 async · pytest + pytest-asyncio. No new dependency.

## Global Constraints

Copied verbatim from `docs/specs/sync-intelligence-engine.md`:
- **Proven over inferred, always labeled.** Phase 2c emits only the **proven, high-confidence** tier: absent-by-patch-id/cherry × shared-subsystem. Every candidate carries `confidence="high"` and an `evidence` list. (The `medium`/`inferred` tiers — PR-aggregate squash-match and file/line similarity — are explicitly deferred to a follow-up; see Notes.)
- **Design-divergence is not work.** A fix touching only `expansion`/`mixed` subsystems must NOT produce a candidate. Only a `shared` touched subsystem graduates a fix to actionable port-debt. This is the load-bearing correctness property, gated in Task 3.
- **Recomputable derived data; raw untouched.** `PortCandidate` is rebuilt idempotently, EXCEPT the human-authored `status` (a dismissed/ported candidate is preserved across recompute). Stale candidates (target acquired the fix) auto-resolve to `ported`.
- **Install-target-agnostic.** Sources/targets come from the harvested `Propagation` matrix, never hard-coded.
- **Match the stack:** async SQLAlchemy 2.0, repository seam for all DB access, 4-space indent, `feat:`-style commit messages, **no AI attribution in commits**.

---

## Builds on existing code

These already exist and MUST be reused as-is (do not redefine):
- `mai.db.models` — `PatchGroup(id, patch_id)`, `Propagation(patch_group_id, core, present, via, confidence, source_sha, evidence)`, `SubsystemClass(subsystem, classification, source)`, `Commit(id, core, sha, ...)`, `CommitFile(commit_id, path, subsystem, added_lines, removed_lines)`. Helpers `_uuid`, `_now`; the imports in `models.py` already cover `String/Text/Integer/Boolean/JSON/ForeignKey/UniqueConstraint/Mapped/mapped_column/datetime`.
- `mai.repository.subsystem_class.SubsystemClassRepository` with `get(subsystem) -> SubsystemClass | None`.
- `mai.sync.propagate.compute_propagation(session)` and `mai.sync.classify.classify_subsystems(session)` — used by tests to build the derived state end-to-end, and chained in `sync-analyze`.
- The CLI `_sync_analyze` coroutine + `sync-analyze` dispatch in `src/mai/cli/__main__.py` (currently runs propagation + classification — this plan adds port candidates as the final stage).
- `tests/conftest.py` — the async in-memory sqlite `session` fixture.

## File Structure

```
src/mai/
  db/models.py                     # MODIFY: add PortCandidate
  sync/portcandidates.py           # compute_port_candidates (offline synthesis pass)
  repository/port_candidate.py     # PortCandidateRepository (status-preserving upsert + auto-resolve helpers)
  cli/__main__.py                  # MODIFY: sync-analyze also computes port candidates
tests/
  test_port_candidate_repository.py
  test_port_candidates.py
  test_port_candidate_validation.py   # the §12 validation gates
```

---

### Task 1: `PortCandidate` model + repository

**Files:**
- Modify: `mai/src/mai/db/models.py` (append one class)
- Create: `mai/src/mai/repository/port_candidate.py`
- Create: `mai/tests/test_port_candidate_repository.py`

**Interfaces:**
- Produces: ORM `PortCandidate(patch_group_id, source_core, target_core, subsystem, classification, magnitude, confidence, evidence, status, source_sha)`;
  `PortCandidateRepository(session)` with `get(patch_group_id, target_core) -> PortCandidate | None`,
  `upsert(patch_group_id, target_core, *, source_core, subsystem, classification, magnitude, confidence, evidence, source_sha) -> None` (preserves existing `status`; new rows start `status="open"`),
  `open_candidates() -> list[PortCandidate]`,
  `mark_status(candidate, status) -> None`.

- [ ] **Step 1: Append the model to `db/models.py`**

Append at the END of `mai/src/mai/db/models.py`:

```python
class PortCandidate(Base):
    """Derived: a fix present in source_core, absent in target_core, in a portable subsystem."""
    __tablename__ = "port_candidate"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    patch_group_id: Mapped[str] = mapped_column(ForeignKey("patch_group.id"))
    source_core: Mapped[str] = mapped_column(String(64))
    target_core: Mapped[str] = mapped_column(String(64))
    subsystem: Mapped[str] = mapped_column(String(255))
    classification: Mapped[str] = mapped_column(String(16))   # the qualifying class (shared)
    magnitude: Mapped[int] = mapped_column(Integer, default=0)  # added+removed lines of the source fix
    confidence: Mapped[str] = mapped_column(String(16), default="high")
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | ported | dismissed
    source_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)

    __table_args__ = (
        UniqueConstraint("patch_group_id", "target_core", name="uq_port_candidate"),
    )
```

- [ ] **Step 2: Write the failing test**

`mai/tests/test_port_candidate_repository.py`:

```python
from sqlalchemy import func, select

from mai.db.models import PatchGroup, PortCandidate
from mai.repository.port_candidate import PortCandidateRepository


async def _pg(session, patch_id="P1") -> str:
    pg = PatchGroup(patch_id=patch_id)
    session.add(pg)
    await session.flush()
    return pg.id


async def test_upsert_inserts_open_then_updates_preserving_status(session):
    pg_id = await _pg(session)
    repo = PortCandidateRepository(session)
    await repo.upsert(pg_id, "two", source_core="three", subsystem="src/shared/Log",
                      classification="shared", magnitude=5, confidence="high",
                      evidence=["e1"], source_sha="abc")
    await session.commit()
    row = await repo.get(pg_id, "two")
    assert row.status == "open" and row.magnitude == 5

    # a human dismisses it
    await repo.mark_status(row, "dismissed")
    await session.commit()
    # recompute upserts again with new magnitude — status must survive
    await repo.upsert(pg_id, "two", source_core="three", subsystem="src/shared/Log",
                      classification="shared", magnitude=9, confidence="high",
                      evidence=["e2"], source_sha="abc")
    await session.commit()
    row = await repo.get(pg_id, "two")
    assert await session.scalar(select(func.count()).select_from(PortCandidate)) == 1
    assert row.status == "dismissed"     # preserved
    assert row.magnitude == 9            # recomputed fields updated


async def test_open_candidates_filters_by_status(session):
    pg_id = await _pg(session)
    repo = PortCandidateRepository(session)
    await repo.upsert(pg_id, "two", source_core="three", subsystem="s",
                      classification="shared", magnitude=1, confidence="high",
                      evidence=[], source_sha="a")
    await repo.upsert(pg_id, "one", source_core="three", subsystem="s",
                      classification="shared", magnitude=1, confidence="high",
                      evidence=[], source_sha="a")
    await session.commit()
    row = await repo.get(pg_id, "one")
    await repo.mark_status(row, "ported")
    await session.commit()
    opens = await repo.open_candidates()
    assert [c.target_core for c in opens] == ["two"]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /c/Users/Roko/Documents/PYTHON/MANGOS/mai && python -m pytest tests/test_port_candidate_repository.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mai.repository.port_candidate'`
(Use `py -3.12 -m pytest ...` if `python` is not 3.12.)

- [ ] **Step 4: Write `repository/port_candidate.py`**

```python
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mai.db.models import PortCandidate


class PortCandidateRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get(self, patch_group_id: str, target_core: str) -> PortCandidate | None:
        return await self._session.scalar(
            select(PortCandidate).where(
                PortCandidate.patch_group_id == patch_group_id,
                PortCandidate.target_core == target_core,
            )
        )

    async def upsert(self, patch_group_id: str, target_core: str, *, source_core: str,
                     subsystem: str, classification: str, magnitude: int,
                     confidence: str, evidence: list, source_sha: str | None) -> None:
        """Insert a new open candidate, or update computed fields preserving `status`."""
        existing = await self.get(patch_group_id, target_core)
        if existing is not None:
            existing.source_core = source_core
            existing.subsystem = subsystem
            existing.classification = classification
            existing.magnitude = magnitude
            existing.confidence = confidence
            existing.evidence = evidence
            existing.source_sha = source_sha
        else:
            self._session.add(PortCandidate(
                patch_group_id=patch_group_id, source_core=source_core,
                target_core=target_core, subsystem=subsystem,
                classification=classification, magnitude=magnitude,
                confidence=confidence, evidence=evidence, source_sha=source_sha))

    async def open_candidates(self) -> list[PortCandidate]:
        return list(await self._session.scalars(
            select(PortCandidate).where(PortCandidate.status == "open")
        ))

    async def mark_status(self, candidate: PortCandidate, status: str) -> None:
        candidate.status = status
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /c/Users/Roko/Documents/PYTHON/MANGOS/mai && python -m pytest tests/test_port_candidate_repository.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add mai/src/mai/db/models.py mai/src/mai/repository/port_candidate.py mai/tests/test_port_candidate_repository.py
git commit -m "feat: PortCandidate model + repository (status-preserving upsert)"
```

---

### Task 2: `compute_port_candidates` synthesis pass

**Files:**
- Create: `mai/src/mai/sync/portcandidates.py`
- Create: `mai/tests/test_port_candidates.py`

**Interfaces:**
- Consumes: `PatchGroup`, `Propagation`, `Commit`, `CommitFile` (existing); `SubsystemClassRepository` (Phase 2b); `PortCandidateRepository` (Task 1).
- Produces: `compute_port_candidates(session) -> dict` with keys `candidates`, `skipped_unportable`, `auto_resolved`.

- [ ] **Step 1: Write the failing test**

`mai/tests/test_port_candidates.py`:

```python
from sqlalchemy import func, select

from mai.db.models import Commit, CommitFile, CommitPatch, PortCandidate
from mai.repository.port_candidate import PortCandidateRepository
from mai.sync.classify import classify_subsystems
from mai.sync.portcandidates import compute_port_candidates
from mai.sync.propagate import compute_propagation


async def _commit(session, core, sha, patch_id, subsystem, added=3, removed=1):
    c = Commit(core=core, sha=sha, author="a", authored_at="t", committer="a",
               committed_at="t", message="m", parent_shas=["p"], is_merge=False)
    session.add(c)
    await session.flush()
    session.add(CommitPatch(commit_id=c.id, patch_id=patch_id))
    session.add(CommitFile(commit_id=c.id, path=f"{subsystem}/f.cpp", change_type="M",
                           added_lines=added, removed_lines=removed, subsystem=subsystem))
    await session.flush()


async def _analyze(session):
    await compute_propagation(session)
    await classify_subsystems(session)
    return await compute_port_candidates(session)


async def test_shared_absent_emits_high_confidence_candidate(session):
    # fix present in three (shared subsystem), absent in two
    await _commit(session, "three", "s_three", "P1", "src/shared/Log", added=4, removed=2)
    await _commit(session, "two", "s_two", "P9", "src/shared/Log")  # gives 'two' a presence in the universe
    await session.commit()

    result = await _analyze(session)
    assert result["candidates"] == 2  # P1→two and P9→three are both shared-absent
    from mai.db.models import PatchGroup
    p1 = await session.scalar(select(PatchGroup).where(PatchGroup.patch_id == "P1"))
    c = await PortCandidateRepository(session).get(p1.id, "two")
    assert c is not None
    assert c.source_core == "three" and c.target_core == "two"
    assert c.classification == "shared" and c.confidence == "high"
    assert c.magnitude == 6  # 4 + 2
    assert c.status == "open" and c.subsystem == "src/shared/Log"
    assert any("three" in e for e in c.evidence)


async def test_expansion_only_fix_emits_no_candidate(session):
    # a fix touching ONLY an expansion subsystem, present in three, absent in two
    await _commit(session, "three", "s_three", "PX", "src/game/Spells")
    await _commit(session, "two", "s_two", "PY", "src/game/Spells")
    await session.commit()
    result = await _analyze(session)
    assert result["candidates"] == 0
    assert result["skipped_unportable"] == 2  # both groups skipped (expansion)
    assert await session.scalar(select(func.count()).select_from(PortCandidate)) == 0


async def test_fully_propagated_fix_emits_no_candidate(session):
    # same patch-id in BOTH forks -> present everywhere -> nothing to port
    await _commit(session, "three", "s_three", "P1", "src/shared/Log")
    await _commit(session, "two", "s_two", "P1", "src/shared/Log")
    await session.commit()
    result = await _analyze(session)
    assert result["candidates"] == 0


async def test_recompute_idempotent_and_auto_resolves_when_ported(session):
    await _commit(session, "three", "s_three", "P1", "src/shared/Log")
    await _commit(session, "two", "s_two", "P9", "src/shared/Log")
    await session.commit()
    await _analyze(session)
    from mai.db.models import PatchGroup
    p1 = await session.scalar(select(PatchGroup).where(PatchGroup.patch_id == "P1"))
    first = await PortCandidateRepository(session).get(p1.id, "two")
    assert first.status == "open"

    # 'two' now acquires P1 (someone ported it) -> recompute should auto-resolve the candidate
    await _commit(session, "two", "s_two_port", "P1", "src/shared/Log")
    await session.commit()
    result = await _analyze(session)
    resolved = await PortCandidateRepository(session).get(p1.id, "two")
    assert resolved.status == "ported"
    assert result["auto_resolved"] >= 1
    # idempotent: no duplicate rows
    assert await session.scalar(
        select(func.count()).select_from(PortCandidate).where(
            PortCandidate.patch_group_id == p1.id, PortCandidate.target_core == "two")) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/Users/Roko/Documents/PYTHON/MANGOS/mai && python -m pytest tests/test_port_candidates.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mai.sync.portcandidates'`

- [ ] **Step 3: Write `sync/portcandidates.py`**

```python
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mai.db.models import Commit, CommitFile, PatchGroup, Propagation
from mai.repository.port_candidate import PortCandidateRepository
from mai.repository.subsystem_class import SubsystemClassRepository


async def compute_port_candidates(session: AsyncSession) -> dict:
    """Synthesize port-debt from the propagation matrix + subsystem classes.

    For every fix (PatchGroup) present in >=1 fork and absent in >=1 fork, whose
    source commit touches a `shared` subsystem, emit one PortCandidate per absent
    target. Candidates whose target later acquires the fix auto-resolve to 'ported'.
    Human `status` (dismissed/ported) is preserved across recompute. Offline.
    """
    rows = (await session.execute(
        select(PatchGroup.id, Propagation.core, Propagation.present,
               Propagation.source_sha)
        .join(Propagation, Propagation.patch_group_id == PatchGroup.id)
    )).all()

    groups: dict[str, dict[str, list]] = defaultdict(lambda: {"present": [], "absent": []})
    for r in rows:
        bucket = "present" if r.present else "absent"
        groups[r.id][bucket].append((r.core, r.source_sha))

    cand_repo = PortCandidateRepository(session)
    sc_repo = SubsystemClassRepository(session)
    current: set[tuple[str, str]] = set()
    skipped = 0

    for pg_id, gd in groups.items():
        if not gd["present"] or not gd["absent"]:
            continue
        source_core, source_sha = min(gd["present"], key=lambda t: t[0])
        commit = await session.scalar(
            select(Commit).where(Commit.core == source_core, Commit.sha == source_sha)
        )
        if commit is None:
            continue
        files = list(await session.scalars(
            select(CommitFile).where(CommitFile.commit_id == commit.id)
        ))
        touched = sorted({f.subsystem for f in files})
        magnitude = sum(f.added_lines + f.removed_lines for f in files)

        shared_subs = []
        for sub in touched:
            sc = await sc_repo.get(sub)
            if sc is not None and sc.classification == "shared":
                shared_subs.append(sub)
        if not shared_subs:
            skipped += 1
            continue

        rep = shared_subs[0]
        absent_cores = sorted(c for c, _ in gd["absent"])
        evidence = [
            f"present in {source_core}@{source_sha}",
            f"shared subsystem {rep}",
            f"absent in {', '.join(absent_cores)}",
        ]
        for target_core in absent_cores:
            await cand_repo.upsert(
                pg_id, target_core, source_core=source_core, subsystem=rep,
                classification="shared", magnitude=magnitude, confidence="high",
                evidence=evidence, source_sha=source_sha)
            current.add((pg_id, target_core))

    auto_resolved = 0
    for cand in await cand_repo.open_candidates():
        if (cand.patch_group_id, cand.target_core) not in current:
            await cand_repo.mark_status(cand, "ported")
            auto_resolved += 1

    await session.commit()
    return {"candidates": len(current), "skipped_unportable": skipped,
            "auto_resolved": auto_resolved}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /c/Users/Roko/Documents/PYTHON/MANGOS/mai && python -m pytest tests/test_port_candidates.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add mai/src/mai/sync/portcandidates.py mai/tests/test_port_candidates.py
git commit -m "feat: compute_port_candidates — proven port-debt from propagation x shared class"
```

---

### Task 3: §12 validation gates (the "prove it's correct" suite)

**Files:**
- Create: `mai/tests/test_port_candidate_validation.py`

**Interfaces:**
- Consumes: the full chain (`compute_propagation`, `classify_subsystems`, `compute_port_candidates`) and `PortCandidateRepository`.

This task is **test-only** — it makes the spec §12 correctness properties explicit and named, so a regression that re-introduces false port-debt (e.g. flagging expansion divergence) fails loudly.

- [ ] **Step 1: Write the validation-gate tests**

`mai/tests/test_port_candidate_validation.py`:

```python
"""Spec §12 validation gates for the port-debt signal (deterministic, in-DB).

Each test pins a correctness property the engine must never regress:
  - design-divergence (expansion) never becomes port-debt
  - a cherry-pick-propagated fix is recognized as present (no candidate)
  - a genuinely-missing shared fix surfaces as a high-confidence candidate
"""
from sqlalchemy import select

from mai.db.models import Commit, CommitFile, CommitPatch, PatchGroup, PortCandidate
from mai.repository.port_candidate import PortCandidateRepository
from mai.sync.classify import classify_subsystems
from mai.sync.portcandidates import compute_port_candidates
from mai.sync.propagate import compute_propagation


async def _commit(session, core, sha, patch_id, subsystem, message="m"):
    c = Commit(core=core, sha=sha, author="a", authored_at="t", committer="a",
               committed_at="t", message=message, parent_shas=["p"], is_merge=False)
    session.add(c)
    await session.flush()
    session.add(CommitPatch(commit_id=c.id, patch_id=patch_id))
    session.add(CommitFile(commit_id=c.id, path=f"{subsystem}/f.cpp", change_type="M",
                           added_lines=2, removed_lines=0, subsystem=subsystem))
    await session.flush()


async def _run(session):
    await compute_propagation(session)
    await classify_subsystems(session)
    return await compute_port_candidates(session)


async def test_gate_design_divergence_is_not_port_debt(session):
    # An expansion-content fix present in three, absent in two, must NOT be flagged.
    await _commit(session, "three", "s3", "PX", "src/game/Spells")
    await _commit(session, "two", "s2", "PZ", "src/game/Quests")
    await session.commit()
    result = await _run(session)
    assert result["candidates"] == 0
    assert await session.scalar(select(PortCandidate)) is None


async def test_gate_cherry_propagated_fix_is_not_port_debt(session):
    # three has the fix (P1, shared); two ported it as a different patch-id but cites
    # three's sha in a cherry trailer -> propagation marks two present -> no candidate.
    a = "a" * 40
    await _commit(session, "three", a, "P1", "src/shared/Log")
    await _commit(session, "two", "b" * 40, "P2", "src/shared/Log",
                  message=f"port\n\n(cherry picked from commit {a})")
    await session.commit()
    result = await _run(session)
    # P1 is present in both (two via cherry); P2 only in two but P2 is shared+absent in three
    # -> exactly the P2->three candidate, and NOT a spurious P1->two candidate.
    p1 = await session.scalar(select(PatchGroup).where(PatchGroup.patch_id == "P1"))
    assert await PortCandidateRepository(session).get(p1.id, "two") is None


async def test_gate_missing_shared_fix_surfaces_high_confidence(session):
    # The positive control: a shared fix in three, absent in two -> one high-confidence candidate.
    await _commit(session, "three", "s3", "P1", "src/shared/Database")
    await _commit(session, "two", "s2", "P9", "src/shared/Database")
    await session.commit()
    await _run(session)
    p1 = await session.scalar(select(PatchGroup).where(PatchGroup.patch_id == "P1"))
    cand = await PortCandidateRepository(session).get(p1.id, "two")
    assert cand is not None
    assert cand.confidence == "high" and cand.classification == "shared"
    assert cand.source_core == "three" and cand.subsystem == "src/shared/Database"
```

- [ ] **Step 2: Run the validation gates**

Run: `cd /c/Users/Roko/Documents/PYTHON/MANGOS/mai && python -m pytest tests/test_port_candidate_validation.py -v`
Expected: PASS (3 passed)

- [ ] **Step 3: Commit**

```bash
git add mai/tests/test_port_candidate_validation.py
git commit -m "test: §12 port-debt validation gates (expansion-safe, cherry-aware, positive control)"
```

---

### Task 4: Extend `sync-analyze` + full-suite green + smoke

**Files:**
- Modify: `mai/src/mai/cli/__main__.py` (extend `_sync_analyze` + its dispatch print)

**Interfaces:**
- Consumes: `compute_propagation`, `classify_subsystems` (existing), `compute_port_candidates` (Task 2).

- [ ] **Step 1: Replace the `_sync_analyze` coroutine in `cli/__main__.py`**

Find the existing coroutine (it currently reads exactly):

```python
async def _sync_analyze() -> dict:
    from mai.sync.classify import classify_subsystems
    from mai.sync.propagate import compute_propagation

    async with SessionFactory() as session:
        propagation = await compute_propagation(session)
        classification = await classify_subsystems(session)
        return {"propagation": propagation, "classification": classification}
```

Replace it with:

```python
async def _sync_analyze() -> dict:
    from mai.sync.classify import classify_subsystems
    from mai.sync.portcandidates import compute_port_candidates
    from mai.sync.propagate import compute_propagation

    async with SessionFactory() as session:
        propagation = await compute_propagation(session)
        classification = await classify_subsystems(session)
        port_candidates = await compute_port_candidates(session)
        return {"propagation": propagation, "classification": classification,
                "port_candidates": port_candidates}
```

- [ ] **Step 2: Replace the `sync-analyze` dispatch branch in `main()`**

Find the existing branch (it currently reads exactly):

```python
    elif args.cmd == "sync-analyze":
        result = asyncio.run(_sync_analyze())
        p, c = result["propagation"], result["classification"]
        print(f"sync-analyze: groups={p['groups']} present={p['present']} "
              f"absent={p['absent']} cherry_links={p['cherry_links']} | "
              f"subsystems={c['total']} shared={c['shared']} "
              f"expansion={c['expansion']} mixed={c['mixed']}")
```

Replace it with:

```python
    elif args.cmd == "sync-analyze":
        result = asyncio.run(_sync_analyze())
        p, c, pc = (result["propagation"], result["classification"],
                    result["port_candidates"])
        print(f"sync-analyze: groups={p['groups']} present={p['present']} "
              f"absent={p['absent']} cherry_links={p['cherry_links']} | "
              f"subsystems={c['total']} shared={c['shared']} "
              f"expansion={c['expansion']} mixed={c['mixed']} | "
              f"port_candidates={pc['candidates']} "
              f"skipped={pc['skipped_unportable']} resolved={pc['auto_resolved']}")
```

- [ ] **Step 3: Run the full test suite**

Run: `cd /c/Users/Roko/Documents/PYTHON/MANGOS/mai && python -m pytest -q`
Expected: PASS — the prior suite (155) plus this plan's new tests (2 + 4 + 3 = 9), so **164 passed** (the 3 `LocalGitClient` tests pass since git is present).

- [ ] **Step 4: Offline smoke — full analyze chain over a fixtured DB**

Write `mai-data/tmp/smoke_portcandidates.py`:

```python
import asyncio

from mai.db.base import Base
from mai.db.models import Commit, CommitFile, CommitPatch
from mai.db.session import SessionFactory, engine
from mai.sync.classify import classify_subsystems
from mai.sync.portcandidates import compute_port_candidates
from mai.sync.propagate import compute_propagation


async def _commit(s, core, sha, patch_id, subsystem):
    c = Commit(core=core, sha=sha, author="a", authored_at="t", committer="a",
               committed_at="t", message="m", parent_shas=["p"], is_merge=False)
    s.add(c)
    await s.flush()
    s.add(CommitPatch(commit_id=c.id, patch_id=patch_id))
    s.add(CommitFile(commit_id=c.id, path=f"{subsystem}/f.cpp", change_type="M",
                     added_lines=3, removed_lines=1, subsystem=subsystem))


async def main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionFactory() as s:
        await _commit(s, "three", "s3", "P1", "src/shared/Log")     # shared, only in three
        await _commit(s, "two", "s2", "PX", "src/game/Spells")      # expansion, only in two
        await s.commit()
        await compute_propagation(s)
        await classify_subsystems(s)
        print(await compute_port_candidates(s))


asyncio.run(main())
```

Run: `cd /c/Users/Roko/Documents/PYTHON/MANGOS/mai && rm -f mai.db && python mai-data/tmp/smoke_portcandidates.py`
Expected: prints `{'candidates': 1, 'skipped_unportable': 1, 'auto_resolved': 0}` (P1 shared→ port to two = 1 candidate; PX expansion → skipped). Then `rm -f mai.db`.

- [ ] **Step 5: Commit**

```bash
git add mai/src/mai/cli/__main__.py
git commit -m "feat: sync-analyze computes port candidates (full propagation+classification+port chain)"
```

---

## Self-Review

- **Spec coverage (Phase 2c of `sync-intelligence-engine.md` §6.2/§13, completes MVP):** `PortCandidate` table (patch_group × source/target core, subsystem, magnitude, confidence, evidence, status) ✓; synthesis from `Propagation` (absent) × `SubsystemClass` (shared) × `CommitFile` (touched + magnitude) ✓; confidence+evidence on every candidate ✓; status preserved + stale auto-resolve ✓; **§12 validation gates** (design-divergence-safe, cherry-aware, positive control) ✓; CLI ✓. **Explicitly deferred (follow-up "2d"):** the PR-aggregate **squash-match** (`CommitPatch.aggregate_of` is still null — computing it needs a git-worker extension to diff PR `base...head`) and the file/line **`inferred`/`medium`** tier. Phase 2c ships the **proven high-confidence** tier only — the MVP correctness bar ("the data is correct" = no false port-debt) is met by precision, not recall. `port_candidates.json` emission is a publish/consumer concern, deferred to the board work.
- **Invariants:** proven+labeled (every candidate `high` + evidence) ✓ · design-divergence-not-work (only `shared` touched subsystem graduates; expansion/mixed-only → skipped; gated in Task 3) ✓ · recomputable + human-status-preserving + stale auto-resolve ✓ · install-target-agnostic (sources/targets from `Propagation`) ✓ · raw untouched (reads only; writes derived `PortCandidate`) ✓.
- **Placeholder scan:** none — every step has runnable code/commands with expected output.
- **Type consistency:** `PortCandidateRepository.get/upsert/open_candidates/mark_status` signatures match between Task 1 definition and the Task 2 / Task 3 / repo-test call sites; `compute_port_candidates(session) -> dict` keys (`candidates/skipped_unportable/auto_resolved`) match the CLI print in Task 4; `SubsystemClassRepository.get(subsystem)` reused as defined in Phase 2b; the Task 4 `_sync_analyze` nests `port_candidates` alongside the existing `propagation`/`classification` keys the dispatch already reads.

## Notes for later plans

- **Phase 2d (coverage expansion, optional):** PR-aggregate **squash-match** — extend the git-worker to compute a patch-id over each PR's `base...head` diff into `CommitPatch.aggregate_of`, then match squash↔multi-commit ports; emit `medium`-confidence candidates via that signal. Plus the file/line-range **similarity `inferred`** tier. These raise *recall*; 2c already guarantees *precision*.
- **Mixed-subsystem file-granularity:** today a `mixed` subsystem never graduates (conservative). When per-file classification exists, a fix touching shared *files* inside a `mixed` subsystem should graduate. Until then, `mixed`-only fixes are deliberately not flagged (no false debt).
- **`src/tools` tuning (carried from 2b):** extractors classify `shared`; re-evaluate `SHARED_PREFIXES` once real extractor subsystems appear in harvested data, so extractor format-magic divergence isn't surfaced as port-debt.
- **Live golden cherry-pick (§12.1 at integration scale):** once r-log forks are actually harvested (Phase 4/5), craft a real cross-fork cherry-pick and assert the live `PortCandidate` output — the deterministic in-DB version is proven here (Task 3).
- **`port_candidates.json`** + the planning board that consumes it: separate `sync-planning-board.md` spec, now unblocked because the port-debt data is trustworthy.
- **Migrations:** new table still relies on `Base.metadata.create_all`; the Postgres/Neon deploy plan introduces Alembic + a baseline.
