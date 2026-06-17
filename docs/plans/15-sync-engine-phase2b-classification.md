# Mai Sync Engine — Phase 2b: Subsystem Classification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Classify every subsystem as **`shared | expansion | mixed`** so that, downstream (Phase 2c), Cata-vs-WotLK *by-design* divergence (expansion content) is never flagged as port-debt — only genuinely shared infrastructure is. A conservative, documented path heuristic, a `SubsystemClass` table, and **manual-override support** that the auto-pass never clobbers.

**Architecture:** A pure classifier (`classify_subsystem`) maps a subsystem path to a class using documented rules seeded from `CLAUDE.md` (infra prefixes → shared; version-bound content segments → expansion; everything else → mixed, resolved at file-granularity in 2c). A `SubsystemClass` derived table (subsystem → classification + source) sits behind a repository whose auto-upsert **preserves `manual_override` rows**. A compute pass (`classify_subsystems`) classifies the distinct subsystems actually present in the harvested `CommitFile` data. `sync-analyze` is extended to run classification alongside propagation.

**Tech Stack:** Python 3.12 · SQLAlchemy 2.0 async · pytest + pytest-asyncio. No new dependency.

## Global Constraints

Copied verbatim from `docs/specs/sync-intelligence-engine.md`:
- **Design-divergence is not work.** Classification exists so expansion-class divergence is recorded as "expected delta," never surfaced as port-debt. The classifier must be **conservative**: only `shared` when confident it's pure infrastructure; only `expansion` when a path segment names version-bound content; everything else `mixed` (Phase 2c resolves mixed at file granularity).
- **Recomputable derived data; raw untouched.** `SubsystemClass` is derived and rebuilt idempotently — EXCEPT rows authored as `manual_override`, which the auto-pass must preserve.
- **Install-target-agnostic.** Subsystems to classify come from harvested data (`distinct CommitFile.subsystem`), never a hard-coded list.
- **Match the stack:** async SQLAlchemy 2.0, repository seam for all DB access, 4-space indent, `feat:`-style commit messages, **no AI attribution in commits**.

---

## Builds on existing code

These already exist and MUST be reused as-is (do not redefine):
- `mai.db.models` — `Commit`, `CommitFile(commit_id, path, change_type, old_path, added_lines, removed_lines, subsystem)`. Helpers `_uuid`, `_now`; imports `String/Text/Integer/Boolean/JSON/ForeignKey/UniqueConstraint/Mapped/mapped_column/datetime` already present in `models.py`.
- The classification knowledge in workspace `CLAUDE.md`: **shared infrastructure** = logging, networking, DB layer, threading (`src/shared/**`, `dep/**`, `src/game/Server/**`); **expansion content** = Cata spells/talents/quests/raids, DBC-bound code, opcode tables.
- The Phase 2a patterns: pure helper in its own module (`sync/cherry.py`) + offline compute pass (`sync/propagate.py`) + repository seam (`repository/propagation.py`); the CLI `_sync_analyze` coroutine + `sync-analyze` dispatch in `src/mai/cli/__main__.py` (currently runs propagation only — this plan extends it).
- `tests/conftest.py` — the async in-memory sqlite `session` fixture.

## File Structure

```
src/mai/
  db/models.py                   # MODIFY: add SubsystemClass
  sync/classify.py               # classify_subsystem (pure) + classify_subsystems (DB pass)
  repository/subsystem_class.py  # SubsystemClassRepository (get / set_manual / upsert_auto)
  cli/__main__.py                # MODIFY: sync-analyze also runs classification
tests/
  test_classify.py
  test_subsystem_class_repository.py
  test_classify_run.py
```

---

### Task 1: Pure `classify_subsystem` heuristic

**Files:**
- Create: `mai/src/mai/sync/classify.py` (pure function only this task)
- Create: `mai/tests/test_classify.py`

**Interfaces:**
- Produces: `classify_subsystem(subsystem: str) -> str` returning `"shared" | "expansion" | "mixed"`; module constants `SHARED_PREFIXES`, `EXPANSION_SEGMENTS`.

- [ ] **Step 1: Write the failing test**

`mai/tests/test_classify.py`:

```python
import pytest

from mai.sync.classify import classify_subsystem


@pytest.mark.parametrize("subsystem", [
    "src/shared/Database",
    "src/shared",
    "dep/recastnavigation/Recast",
    "src/realmd",
    "src/tools/Extractor_projects",
    "src/framework/Threading",
])
def test_shared_infrastructure(subsystem):
    assert classify_subsystem(subsystem) == "shared"


@pytest.mark.parametrize("subsystem", [
    "src/game/Spells",
    "src/game/Object/Quests",
    "src/game/BattleGround",
    "src/game/Arena",
    "src/game/Talents",
    "src/game/Loot",
])
def test_expansion_content(subsystem):
    assert classify_subsystem(subsystem) == "expansion"


@pytest.mark.parametrize("subsystem", [
    "src/game/Object",
    "src/game/Server",       # mixes shared socket plumbing + expansion-bound opcode router
    "src/game/Maps",
    "(root)",
])
def test_mixed_default(subsystem):
    assert classify_subsystem(subsystem) == "mixed"


def test_case_insensitive():
    assert classify_subsystem("SRC/SHARED/Log") == "shared"
    assert classify_subsystem("src/game/SPELLS") == "expansion"


def test_dep_prefix_not_confused_by_substring():
    # a path that merely starts with the letters "dep" but isn't the dep/ tree
    assert classify_subsystem("src/game/Dependencies") == "mixed"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/Users/Roko/Documents/PYTHON/MANGOS/mai && python -m pytest tests/test_classify.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mai.sync.classify'`
(Use `py -3.12 -m pytest ...` if `python` is not 3.12.)

- [ ] **Step 3: Write `sync/classify.py`**

```python
# Conservative subsystem classifier. Rules seeded from workspace CLAUDE.md:
# pure-infrastructure prefixes are shared; path segments naming version-bound
# (Cata-vs-WotLK) content are expansion; everything else is mixed and is
# resolved at file granularity downstream (Phase 2c).
SHARED_PREFIXES = ("src/shared", "dep", "src/realmd", "src/tools", "src/framework")

EXPANSION_SEGMENTS = frozenset({
    "spell", "spells", "quest", "quests", "talent", "talents",
    "achievement", "achievements", "battleground", "battlegrounds",
    "arena", "arenas", "loot", "pet", "pets", "vehicle", "vehicles",
    "reputation", "scripts",
})


def classify_subsystem(subsystem: str) -> str:
    """Return 'shared' | 'expansion' | 'mixed' for a subsystem path (depth-3 dir).

    Conservative by design: 'shared' only for infrastructure prefixes, 'expansion'
    only when a path segment names version-bound content, else 'mixed'.
    """
    s = subsystem.lower()
    for prefix in SHARED_PREFIXES:
        if s == prefix or s.startswith(prefix + "/"):
            return "shared"
    if any(seg in EXPANSION_SEGMENTS for seg in s.split("/")):
        return "expansion"
    return "mixed"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /c/Users/Roko/Documents/PYTHON/MANGOS/mai && python -m pytest tests/test_classify.py -v`
Expected: PASS (all parametrized cases pass — 18 passed)

- [ ] **Step 5: Commit**

```bash
git add mai/src/mai/sync/classify.py mai/tests/test_classify.py
git commit -m "feat: conservative subsystem classifier (shared/expansion/mixed)"
```

---

### Task 2: `SubsystemClass` model + repository

**Files:**
- Modify: `mai/src/mai/db/models.py` (append one class)
- Create: `mai/src/mai/repository/subsystem_class.py`
- Create: `mai/tests/test_subsystem_class_repository.py`

**Interfaces:**
- Produces: ORM `SubsystemClass(subsystem, classification, source)`;
  `SubsystemClassRepository(session)` with `get(subsystem) -> SubsystemClass | None`,
  `set_manual(subsystem, classification) -> None` (source `manual_override`),
  `upsert_auto(subsystem, classification, source="heuristic") -> bool` (returns False and preserves the row when it is `manual_override`).

- [ ] **Step 1: Append the model to `db/models.py`**

Append at the END of `mai/src/mai/db/models.py` (all needed imports already exist there):

```python
class SubsystemClass(Base):
    """Derived: a subsystem's portability class. Auto-classified, manually overridable."""
    __tablename__ = "subsystem_class"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    subsystem: Mapped[str] = mapped_column(String(255), unique=True)
    classification: Mapped[str] = mapped_column(String(16))   # shared | expansion | mixed
    source: Mapped[str] = mapped_column(String(16))           # seed | heuristic | ai | manual_override
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)
```

- [ ] **Step 2: Write the failing test**

`mai/tests/test_subsystem_class_repository.py`:

```python
from sqlalchemy import func, select

from mai.db.models import SubsystemClass
from mai.repository.subsystem_class import SubsystemClassRepository


async def test_upsert_auto_inserts_then_updates_single_row(session):
    repo = SubsystemClassRepository(session)
    assert await repo.upsert_auto("src/game/Object", "mixed") is True
    assert await repo.upsert_auto("src/game/Object", "shared") is True
    await session.commit()
    assert await session.scalar(select(func.count()).select_from(SubsystemClass)) == 1
    row = await repo.get("src/game/Object")
    assert row.classification == "shared" and row.source == "heuristic"


async def test_set_manual_then_auto_is_preserved(session):
    repo = SubsystemClassRepository(session)
    await repo.set_manual("src/game/Server", "shared")
    await session.commit()
    # a later auto-pass must NOT clobber the manual override
    assert await repo.upsert_auto("src/game/Server", "mixed") is False
    await session.commit()
    row = await repo.get("src/game/Server")
    assert row.classification == "shared" and row.source == "manual_override"


async def test_get_missing_returns_none(session):
    assert await SubsystemClassRepository(session).get("nope") is None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /c/Users/Roko/Documents/PYTHON/MANGOS/mai && python -m pytest tests/test_subsystem_class_repository.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mai.repository.subsystem_class'`

- [ ] **Step 4: Write `repository/subsystem_class.py`**

```python
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mai.db.models import SubsystemClass


class SubsystemClassRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get(self, subsystem: str) -> SubsystemClass | None:
        return await self._session.scalar(
            select(SubsystemClass).where(SubsystemClass.subsystem == subsystem)
        )

    async def set_manual(self, subsystem: str, classification: str) -> None:
        existing = await self.get(subsystem)
        if existing:
            existing.classification = classification
            existing.source = "manual_override"
        else:
            self._session.add(SubsystemClass(
                subsystem=subsystem, classification=classification,
                source="manual_override"))

    async def upsert_auto(self, subsystem: str, classification: str,
                          source: str = "heuristic") -> bool:
        """Insert/update an auto classification. Preserve a manual_override row.

        Returns True if written, False if an existing manual_override was kept.
        """
        existing = await self.get(subsystem)
        if existing is not None and existing.source == "manual_override":
            return False
        if existing is not None:
            existing.classification = classification
            existing.source = source
        else:
            self._session.add(SubsystemClass(
                subsystem=subsystem, classification=classification, source=source))
        return True
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /c/Users/Roko/Documents/PYTHON/MANGOS/mai && python -m pytest tests/test_subsystem_class_repository.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add mai/src/mai/db/models.py mai/src/mai/repository/subsystem_class.py mai/tests/test_subsystem_class_repository.py
git commit -m "feat: SubsystemClass model + repository (manual-override preserving upsert)"
```

---

### Task 3: `classify_subsystems` compute pass

**Files:**
- Modify: `mai/src/mai/sync/classify.py` (append the DB pass)
- Create: `mai/tests/test_classify_run.py`

**Interfaces:**
- Consumes: `classify_subsystem` (Task 1); `CommitFile` (Phase 1); `SubsystemClassRepository` (Task 2).
- Produces: `classify_subsystems(session) -> dict` with keys `total`, `shared`, `expansion`, `mixed`, `manual_preserved`.

- [ ] **Step 1: Write the failing test**

`mai/tests/test_classify_run.py`:

```python
from sqlalchemy import func, select

from mai.db.models import Commit, CommitFile, SubsystemClass
from mai.repository.subsystem_class import SubsystemClassRepository
from mai.sync.classify import classify_subsystems


async def _file(session, subsystem, path):
    c = Commit(core="three", sha=f"sha-{subsystem}-{path}", author="a",
               authored_at="t", committer="a", committed_at="t", message="m",
               parent_shas=["p"], is_merge=False)
    session.add(c)
    await session.flush()
    session.add(CommitFile(commit_id=c.id, path=path, change_type="M",
                           added_lines=1, removed_lines=0, subsystem=subsystem))
    await session.flush()


async def test_classifies_distinct_subsystems(session):
    await _file(session, "src/shared/Database", "src/shared/Database/Field.cpp")
    await _file(session, "src/game/Spells", "src/game/Spells/Spell.cpp")
    await _file(session, "src/game/Object", "src/game/Object/Player.cpp")
    await _file(session, "src/game/Object", "src/game/Object/Unit.cpp")  # dup subsystem
    await session.commit()

    result = await classify_subsystems(session)
    assert result["total"] == 3        # three distinct subsystems
    assert result["shared"] == 1 and result["expansion"] == 1 and result["mixed"] == 1
    repo = SubsystemClassRepository(session)
    assert (await repo.get("src/shared/Database")).classification == "shared"
    assert (await repo.get("src/game/Spells")).classification == "expansion"
    assert (await repo.get("src/game/Object")).classification == "mixed"


async def test_preserves_manual_override(session):
    await _file(session, "src/game/Server", "src/game/Server/WorldSocket.cpp")
    await session.commit()
    await SubsystemClassRepository(session).set_manual("src/game/Server", "shared")
    await session.commit()

    result = await classify_subsystems(session)
    assert result["manual_preserved"] == 1
    row = await SubsystemClassRepository(session).get("src/game/Server")
    assert row.classification == "shared" and row.source == "manual_override"


async def test_recompute_is_idempotent(session):
    await _file(session, "src/game/Spells", "src/game/Spells/Spell.cpp")
    await session.commit()
    await classify_subsystems(session)
    await classify_subsystems(session)
    assert await session.scalar(select(func.count()).select_from(SubsystemClass)) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/Users/Roko/Documents/PYTHON/MANGOS/mai && python -m pytest tests/test_classify_run.py -v`
Expected: FAIL with `ImportError: cannot import name 'classify_subsystems' from 'mai.sync.classify'`

- [ ] **Step 3: Append `classify_subsystems` to `sync/classify.py`**

Add these imports at the TOP of `mai/src/mai/sync/classify.py` (above the existing constants):

```python
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mai.db.models import CommitFile
from mai.repository.subsystem_class import SubsystemClassRepository
```

Then append at the END of the file (after `classify_subsystem`):

```python
async def classify_subsystems(session: AsyncSession) -> dict:
    """Classify every distinct harvested subsystem, preserving manual overrides.

    Reads `distinct CommitFile.subsystem` (offline), applies `classify_subsystem`,
    and upserts SubsystemClass rows. A row authored as `manual_override` is kept
    and counted under its existing classification. Recomputable.
    """
    subsystems = sorted(
        await session.scalars(select(CommitFile.subsystem).distinct())
    )
    repo = SubsystemClassRepository(session)
    counts = {"total": 0, "shared": 0, "expansion": 0, "mixed": 0, "manual_preserved": 0}
    for subsystem in subsystems:
        auto = classify_subsystem(subsystem)
        wrote = await repo.upsert_auto(subsystem, auto)
        if wrote:
            counts[auto] += 1
        else:
            counts["manual_preserved"] += 1
            kept = await repo.get(subsystem)
            counts[kept.classification] += 1
        counts["total"] += 1
    await session.commit()
    return counts
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /c/Users/Roko/Documents/PYTHON/MANGOS/mai && python -m pytest tests/test_classify_run.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add mai/src/mai/sync/classify.py mai/tests/test_classify_run.py
git commit -m "feat: classify_subsystems pass over harvested subsystems (manual-override safe)"
```

---

### Task 4: Extend `sync-analyze` to classify + full-suite green + smoke

**Files:**
- Modify: `mai/src/mai/cli/__main__.py` (extend `_sync_analyze` + its dispatch print)

**Interfaces:**
- Consumes: `compute_propagation` (Phase 2a), `classify_subsystems` (Task 3).

- [ ] **Step 1: Replace the `_sync_analyze` coroutine in `cli/__main__.py`**

Find the existing coroutine (it currently reads exactly):

```python
async def _sync_analyze() -> dict:
    from mai.sync.propagate import compute_propagation

    async with SessionFactory() as session:
        return await compute_propagation(session)
```

Replace it with:

```python
async def _sync_analyze() -> dict:
    from mai.sync.classify import classify_subsystems
    from mai.sync.propagate import compute_propagation

    async with SessionFactory() as session:
        propagation = await compute_propagation(session)
        classification = await classify_subsystems(session)
        return {"propagation": propagation, "classification": classification}
```

- [ ] **Step 2: Replace the `sync-analyze` dispatch branch in `main()`**

Find the existing branch (it currently reads exactly):

```python
    elif args.cmd == "sync-analyze":
        result = asyncio.run(_sync_analyze())
        print(f"sync-analyze: groups={result['groups']} present={result['present']} "
              f"absent={result['absent']} cherry_links={result['cherry_links']}")
```

Replace it with:

```python
    elif args.cmd == "sync-analyze":
        result = asyncio.run(_sync_analyze())
        p, c = result["propagation"], result["classification"]
        print(f"sync-analyze: groups={p['groups']} present={p['present']} "
              f"absent={p['absent']} cherry_links={p['cherry_links']} | "
              f"subsystems={c['total']} shared={c['shared']} "
              f"expansion={c['expansion']} mixed={c['mixed']}")
```

- [ ] **Step 3: Run the full test suite**

Run: `cd /c/Users/Roko/Documents/PYTHON/MANGOS/mai && python -m pytest -q`
Expected: PASS — the prior suite (131) plus this plan's new tests (18 + 3 + 3 = 24), so **155 passed** (the 3 `LocalGitClient` tests pass since git is present).

- [ ] **Step 4: Offline smoke — classification over a fixtured DB**

Write `mai-data/tmp/smoke_classify.py`:

```python
import asyncio

from mai.db.base import Base
from mai.db.models import Commit, CommitFile
from mai.db.session import SessionFactory, engine
from mai.sync.classify import classify_subsystems


async def main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionFactory() as s:
        for subsystem, path in [("src/shared/Log", "src/shared/Log/Log.cpp"),
                                ("src/game/Spells", "src/game/Spells/Spell.cpp"),
                                ("src/game/Object", "src/game/Object/Player.cpp")]:
            c = Commit(core="three", sha=f"x-{subsystem}", author="a", authored_at="t",
                       committer="a", committed_at="t", message="m",
                       parent_shas=["p"], is_merge=False)
            s.add(c)
            await s.flush()
            s.add(CommitFile(commit_id=c.id, path=path, change_type="M",
                             added_lines=1, removed_lines=0, subsystem=subsystem))
        await s.commit()
        print(await classify_subsystems(s))


asyncio.run(main())
```

Run: `cd /c/Users/Roko/Documents/PYTHON/MANGOS/mai && rm -f mai.db && python mai-data/tmp/smoke_classify.py`
Expected: prints `{'total': 3, 'shared': 1, 'expansion': 1, 'mixed': 1, 'manual_preserved': 0}`. Then `rm -f mai.db` to leave no artifact.

- [ ] **Step 5: Commit**

```bash
git add mai/src/mai/cli/__main__.py
git commit -m "feat: sync-analyze also classifies subsystems (propagation + classification)"
```

---

## Self-Review

- **Spec coverage (Phase 2b of `sync-intelligence-engine.md` §6.2/§13):** `SubsystemClass` table (subsystem → `classification` + `source`) ✓; conservative `shared|expansion|mixed` classifier seeded from `CLAUDE.md` paths ✓; **manual-override support** (auto-pass preserves `manual_override`) ✓; offline compute over `distinct CommitFile.subsystem` ✓; CLI surface ✓. **Out of scope by design (2c):** `PortCandidate`, file-granularity resolution of `mixed`, PR-aggregate squash-match, the AI portability tag (`source="ai"` value is defined in the column but not produced here), and the §12 validation gates.
- **Invariants:** design-divergence-not-work (classifier is the filter; conservative `mixed` default never over-flags) ✓ · recomputable + manual-preserving (idempotent `upsert_auto`, `manual_override` kept) ✓ · install-target-agnostic (subsystems from harvested `CommitFile`) ✓ · raw untouched (reads `CommitFile`, writes only the derived `SubsystemClass`) ✓.
- **Placeholder scan:** none — every step has runnable code/commands with expected output.
- **Type consistency:** `classify_subsystem(subsystem) -> str` used identically in `classify_subsystems` and tests; `SubsystemClassRepository.get/set_manual/upsert_auto` signatures match between Task 2 definition and Task 3 calls; `classify_subsystems(session) -> dict` keys (`total/shared/expansion/mixed/manual_preserved`) match the CLI print in Task 4; the Task 4 replacement of `_sync_analyze` keeps the propagation keys (`groups/present/absent/cherry_links`) under a `propagation` sub-dict that the new print reads.

## Notes for later plans (2c)

- **Phase 2c (`16-...`): `PortCandidate`** — join `Propagation` (absent rows) × `SubsystemClass` × `CommitFile` (the touched files of the source fix): a fix graduates to actionable port-debt only when its subsystem is `shared`, OR it is `mixed` AND the specific touched files resolve to shared (file-granularity resolution — the reason `mixed` exists). Add the PR-aggregate squash-match (`CommitPatch.aggregate_of`, `via="squash_match"`, `medium` confidence) and the file+line-range `inferred` tier; carry `confidence`+`evidence` onto each candidate; extend `sync-analyze` to emit `port_candidates.json`. Wire §12 validation gates (design-divergence anchor uses `SubsystemClass`; live golden-cherry-pick once r-log forks are harvested). **← MVP boundary: "the data is correct."**
- **Classifier tuning:** the `SHARED_PREFIXES` / `EXPANSION_SEGMENTS` lists are the single tuning surface; expect to refine against the real distinct-subsystem set once forks are harvested. A `source="seed"` curated table and the `source="ai"` portability tag are future refinements above the heuristic.
- **Migrations:** new table still relies on `Base.metadata.create_all`; the Postgres/Neon deploy plan introduces Alembic + a baseline.
