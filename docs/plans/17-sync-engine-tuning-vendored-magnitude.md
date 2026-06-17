# Mai Sync Engine — Tuning: Vendored Class + Magnitude Tiers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sharpen the port-debt backlog from "correct" to "actionable," using two precision fixes the first **real-fork run** revealed: (1) a new **`vendored`** subsystem class so `dep/` third-party libraries (structural in-tree-vs-submodule divergence, e.g. an 824k-line `dep` "candidate") stop graduating to port-debt; (2) a **magnitude `tier`** on each PortCandidate (`surgical`/`small`/`moderate`/`bulk`) so surgical fixes are filterable/rankable and bulk imports are visibly demoted — without dropping anything.

**Architecture:** Pure additions on top of the merged Phase 2c engine. `classify_subsystem` gains a `vendored` branch (checked before `shared`); since port candidates only graduate on `classification == "shared"`, vendored fixes are excluded automatically. A pure `magnitude_tier(n)` helper bands a candidate's magnitude; `PortCandidateRepository.upsert` stamps the `tier` from the magnitude it already receives (no caller change); `compute_port_candidates` reports the tier distribution.

**Tech Stack:** Python 3.12 · SQLAlchemy 2.0 async · pytest + pytest-asyncio. No new dependency.

## Global Constraints

From `docs/specs/sync-intelligence-engine.md` + the real-run findings:
- **Design-divergence / structural divergence is not work.** `dep/` is vendored third-party code whose cross-fork difference is structural (CLAUDE.md: three bundles deps in-tree, two uses a submodule) — never actionable port-debt. It gets its own `vendored` class, distinct from `shared`/`expansion`/`mixed`.
- **`src/tools` stays `shared`** — the real run showed it yields legitimate small extractor fixes (magnitude 2–596); do NOT reclassify it.
- **No silent caps.** Magnitude tiers LABEL candidates; nothing is dropped. The full backlog stays queryable; consumers filter by tier.
- **Recomputable; conservative.** Classifier still defaults unknowns to `mixed`; only the new `dep/` prefix changes. Port candidates still graduate only on `shared`.
- **Match the stack:** async SQLAlchemy 2.0, repository seam, 4-space indent, `feat:`-style commits, **no AI attribution**.

---

## Builds on existing code

These exist and MUST be reused as-is (do not redefine):
- `mai.sync.classify` — `SHARED_PREFIXES`, `EXPANSION_SEGMENTS`, pure `classify_subsystem(subsystem) -> str`, and async `classify_subsystems(session) -> dict` (returns `total/shared/expansion/mixed/manual_preserved`).
- `mai.db.models.PortCandidate` (fields incl. `magnitude`, no `tier` yet) and `mai.repository.port_candidate.PortCandidateRepository.upsert(patch_group_id, target_core, *, source_core, subsystem, classification, magnitude, confidence, evidence, source_sha)`.
- `mai.sync.portcandidates.compute_port_candidates(session) -> dict` (returns `candidates/skipped_unportable/auto_resolved`); it graduates a fix only when a touched subsystem has `SubsystemClass.classification == "shared"`.
- `tests/conftest.py` — async in-memory sqlite `session` fixture.

## File Structure

```
src/mai/
  sync/classify.py                 # MODIFY: add VENDORED_PREFIXES + vendored branch; count vendored
  db/models.py                     # MODIFY: add PortCandidate.tier
  repository/port_candidate.py     # MODIFY: magnitude_tier() helper + stamp tier in upsert
  sync/portcandidates.py           # MODIFY: report tier distribution in return dict
  cli/__main__.py                  # MODIFY: sync-analyze prints vendored + tier bands
tests/
  test_classify.py                 # MODIFY: vendored cases
  test_classify_run.py             # MODIFY: vendored count
  test_port_candidate_repository.py# MODIFY: tier assertions
  test_port_candidates.py          # MODIFY: vendored-excluded + tier distribution
```

---

### Task 1: `vendored` classification for `dep/`

**Files:**
- Modify: `mai/src/mai/sync/classify.py`
- Modify: `mai/tests/test_classify.py`
- Modify: `mai/tests/test_classify_run.py`

**Interfaces:**
- Produces: module constant `VENDORED_PREFIXES`; `classify_subsystem` may now return `"vendored"`; `classify_subsystems` return dict gains a `vendored` count.

- [ ] **Step 1: Add the failing test cases**

In `mai/tests/test_classify.py`, add a new parametrized test (place it after `test_shared_infrastructure`):

```python
@pytest.mark.parametrize("subsystem", [
    "dep",
    "dep/bzip2",
    "dep/StormLib/src",
    "dep/recastnavigation",
])
def test_vendored_dependencies(subsystem):
    assert classify_subsystem(subsystem) == "vendored"
```

And confirm `src/tools` is NOT vendored — add to the existing `test_shared_infrastructure` parametrize list (it already asserts `== "shared"`) is fine; additionally add this explicit guard test below it:

```python
def test_tools_stays_shared_not_vendored():
    assert classify_subsystem("src/tools/Extractor_projects") == "shared"
```

In `mai/tests/test_classify_run.py`, update `test_classifies_distinct_subsystems`: add a `dep/` file and assert the `vendored` count. Replace the body of that test with:

```python
async def test_classifies_distinct_subsystems(session):
    await _file(session, "src/shared/Database", "src/shared/Database/Field.cpp")
    await _file(session, "src/game/Spells", "src/game/Spells/Spell.cpp")
    await _file(session, "src/game/Object", "src/game/Object/Player.cpp")
    await _file(session, "src/game/Object", "src/game/Object/Unit.cpp")  # dup subsystem
    await _file(session, "dep/zlib", "dep/zlib/inflate.c")
    await session.commit()

    result = await classify_subsystems(session)
    assert result["total"] == 4        # four distinct subsystems
    assert result["shared"] == 1 and result["expansion"] == 1
    assert result["mixed"] == 1 and result["vendored"] == 1
    repo = SubsystemClassRepository(session)
    assert (await repo.get("src/shared/Database")).classification == "shared"
    assert (await repo.get("src/game/Spells")).classification == "expansion"
    assert (await repo.get("src/game/Object")).classification == "mixed"
    assert (await repo.get("dep/zlib")).classification == "vendored"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /c/Users/Roko/Documents/PYTHON/MANGOS/mai && python -m pytest tests/test_classify.py tests/test_classify_run.py -v`
Expected: FAIL — `test_vendored_dependencies` gets `"shared"` (dep currently in SHARED_PREFIXES); `test_classifies_distinct_subsystems` KeyErrors on `result["vendored"]`.
(Use `py -3.12 -m pytest ...` if `python` is not 3.12.)

- [ ] **Step 3: Update `sync/classify.py`**

In `mai/src/mai/sync/classify.py`: (a) remove `"dep"` from `SHARED_PREFIXES`; (b) add a `VENDORED_PREFIXES` constant; (c) add the vendored branch FIRST in `classify_subsystem`; (d) seed the `vendored` counter in `classify_subsystems`.

`SHARED_PREFIXES` becomes (drop `"dep"`):

```python
SHARED_PREFIXES = ("src/shared", "src/realmd", "src/tools", "src/framework")
```

Add this constant immediately after `SHARED_PREFIXES`:

```python
# Vendored third-party libraries: cross-fork difference is structural (in-tree vs
# submodule), never actionable port-debt. Classified apart from shared/expansion/mixed.
VENDORED_PREFIXES = ("dep",)
```

In `classify_subsystem`, add the vendored check as the FIRST rule (before the shared loop). The function body becomes:

```python
    s = subsystem.lower()
    for prefix in VENDORED_PREFIXES:
        if s == prefix or s.startswith(prefix + "/"):
            return "vendored"
    for prefix in SHARED_PREFIXES:
        if s == prefix or s.startswith(prefix + "/"):
            return "shared"
    if any(seg in EXPANSION_SEGMENTS for seg in s.split("/")):
        return "expansion"
    return "mixed"
```

In `classify_subsystems`, add `"vendored": 0` to the `counts` dict initializer so it reads:

```python
    counts = {"total": 0, "shared": 0, "expansion": 0, "mixed": 0,
              "vendored": 0, "manual_preserved": 0}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /c/Users/Roko/Documents/PYTHON/MANGOS/mai && python -m pytest tests/test_classify.py tests/test_classify_run.py -v`
Expected: PASS (all green — vendored cases + updated run test).

- [ ] **Step 5: Commit**

```bash
git add mai/src/mai/sync/classify.py mai/tests/test_classify.py mai/tests/test_classify_run.py
git commit -m "feat: vendored subsystem class for dep/ (excludes third-party libs from port-debt)"
```

---

### Task 2: `magnitude_tier` helper + `PortCandidate.tier` column

**Files:**
- Modify: `mai/src/mai/db/models.py` (add `tier` to `PortCandidate`)
- Modify: `mai/src/mai/repository/port_candidate.py` (helper + stamp tier)
- Modify: `mai/tests/test_port_candidate_repository.py`

**Interfaces:**
- Produces: `magnitude_tier(magnitude: int) -> str` (`surgical|small|moderate|bulk`); `PortCandidate.tier`; `upsert` now stamps `tier` from `magnitude` on both insert and update.

- [ ] **Step 1: Add the `tier` column to `PortCandidate` in `db/models.py`**

In `mai/src/mai/db/models.py`, in the `PortCandidate` class, add a `tier` column immediately after the `magnitude` column:

```python
    tier: Mapped[str] = mapped_column(String(16), default="surgical")  # surgical|small|moderate|bulk
```

- [ ] **Step 2: Write the failing test**

In `mai/tests/test_port_candidate_repository.py`, add these tests at the end of the file:

```python
from mai.repository.port_candidate import magnitude_tier


def test_magnitude_tier_bands():
    assert magnitude_tier(0) == "surgical"
    assert magnitude_tier(50) == "surgical"
    assert magnitude_tier(51) == "small"
    assert magnitude_tier(500) == "small"
    assert magnitude_tier(501) == "moderate"
    assert magnitude_tier(5000) == "moderate"
    assert magnitude_tier(5001) == "bulk"
    assert magnitude_tier(824466) == "bulk"


async def test_upsert_stamps_tier_from_magnitude(session):
    pg = PatchGroup(patch_id="PT")
    session.add(pg)
    await session.flush()
    repo = PortCandidateRepository(session)
    await repo.upsert(pg.id, "two", source_core="three", subsystem="src/shared/Log",
                      classification="shared", magnitude=12, confidence="high",
                      evidence=[], source_sha="a")
    await session.commit()
    assert (await repo.get(pg.id, "two")).tier == "surgical"
    # recompute with a bulk magnitude -> tier updates
    await repo.upsert(pg.id, "two", source_core="three", subsystem="src/shared/Log",
                      classification="shared", magnitude=9000, confidence="high",
                      evidence=[], source_sha="a")
    await session.commit()
    assert (await repo.get(pg.id, "two")).tier == "bulk"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /c/Users/Roko/Documents/PYTHON/MANGOS/mai && python -m pytest tests/test_port_candidate_repository.py -v`
Expected: FAIL with `ImportError: cannot import name 'magnitude_tier'`

- [ ] **Step 4: Update `repository/port_candidate.py`**

Add the pure helper at the TOP of `mai/src/mai/repository/port_candidate.py` (after the imports, before the class):

```python
def magnitude_tier(magnitude: int) -> str:
    """Band a candidate's line-magnitude. surgical<=50<small<=500<moderate<=5000<bulk."""
    if magnitude <= 50:
        return "surgical"
    if magnitude <= 500:
        return "small"
    if magnitude <= 5000:
        return "moderate"
    return "bulk"
```

Then, in `PortCandidateRepository.upsert`, stamp `tier` on BOTH paths. The existing-row branch gains:

```python
            existing.tier = magnitude_tier(magnitude)
```

(place it right after `existing.magnitude = magnitude`). The insert branch passes `tier=` to the constructor:

```python
            self._session.add(PortCandidate(
                patch_group_id=patch_group_id, source_core=source_core,
                target_core=target_core, subsystem=subsystem,
                classification=classification, magnitude=magnitude,
                tier=magnitude_tier(magnitude),
                confidence=confidence, evidence=evidence, source_sha=source_sha))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /c/Users/Roko/Documents/PYTHON/MANGOS/mai && python -m pytest tests/test_port_candidate_repository.py -v`
Expected: PASS (all green — tier bands + tier stamping).

- [ ] **Step 6: Commit**

```bash
git add mai/src/mai/db/models.py mai/src/mai/repository/port_candidate.py mai/tests/test_port_candidate_repository.py
git commit -m "feat: magnitude tier on PortCandidate (surgical/small/moderate/bulk)"
```

---

### Task 3: tier distribution in `compute_port_candidates` + end-to-end vendored exclusion

**Files:**
- Modify: `mai/src/mai/sync/portcandidates.py`
- Modify: `mai/tests/test_port_candidates.py`

**Interfaces:**
- Produces: `compute_port_candidates(session) -> dict` return dict gains a `tiers` key — a dict `{surgical, small, moderate, bulk}` counting OPEN candidates by tier.

- [ ] **Step 1: Add/Update the failing tests**

In `mai/tests/test_port_candidates.py`, add a new test (after the existing ones):

```python
async def test_vendored_fix_emits_no_candidate(session):
    # a fix touching only a vendored (dep/) subsystem must NOT graduate
    await _commit(session, "three", "s3", "PV", "dep/zlib")
    await _commit(session, "two", "s2", "PW", "dep/zlib")
    await session.commit()
    result = await _analyze(session)
    assert result["candidates"] == 0
    assert result["skipped_unportable"] == 2


async def test_tier_distribution_reported(session):
    # one surgical (mag 2) shared fix present in three, absent in two
    await _commit(session, "three", "s3", "P1", "src/shared/Log", added=1, removed=1)
    await _commit(session, "two", "s2", "P9", "src/shared/Log", added=1, removed=1)
    await session.commit()
    result = await _analyze(session)
    assert "tiers" in result
    assert result["tiers"]["surgical"] == 2   # both P1->two and P9->three are tiny
    assert result["tiers"]["bulk"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /c/Users/Roko/Documents/PYTHON/MANGOS/mai && python -m pytest tests/test_port_candidates.py -v`
Expected: FAIL — `test_vendored_fix_emits_no_candidate` fails today (dep currently classifies shared → emits candidates) UNLESS Task 1 is merged; since Task 1 IS already done on this branch, this test should fail only on the missing `tiers` key for `test_tier_distribution_reported` (`KeyError: 'tiers'`). If `test_vendored_fix_emits_no_candidate` already passes, that confirms Task 1's vendored class is in effect — good.

- [ ] **Step 3: Update `sync/portcandidates.py`**

In `mai/src/mai/sync/portcandidates.py`, add the tier import at the top:

```python
from mai.repository.port_candidate import PortCandidateRepository, magnitude_tier
```

(replace the existing `from mai.repository.port_candidate import PortCandidateRepository` line).

Then, at the END of `compute_port_candidates`, compute the tier distribution over the final OPEN candidates and add it to the return. Replace the closing block:

```python
    candidates = len(await cand_repo.open_candidates())
    await session.commit()
    return {"candidates": candidates, "skipped_unportable": skipped,
            "auto_resolved": auto_resolved}
```

with:

```python
    open_now = await cand_repo.open_candidates()
    tiers = {"surgical": 0, "small": 0, "moderate": 0, "bulk": 0}
    for c in open_now:
        tiers[magnitude_tier(c.magnitude)] += 1
    await session.commit()
    return {"candidates": len(open_now), "skipped_unportable": skipped,
            "auto_resolved": auto_resolved, "tiers": tiers}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /c/Users/Roko/Documents/PYTHON/MANGOS/mai && python -m pytest tests/test_port_candidates.py tests/test_port_candidate_validation.py -v`
Expected: PASS (existing port-candidate + validation tests still green, plus the two new ones).

- [ ] **Step 5: Commit**

```bash
git add mai/src/mai/sync/portcandidates.py mai/tests/test_port_candidates.py
git commit -m "feat: report port-candidate tier distribution; vendored fixes excluded end-to-end"
```

---

### Task 4: CLI surface + full-suite green + smoke

**Files:**
- Modify: `mai/src/mai/cli/__main__.py` (extend the `sync-analyze` print)

**Interfaces:**
- Consumes: the updated `classify_subsystems` (now returns `vendored`) and `compute_port_candidates` (now returns `tiers`).

- [ ] **Step 1: Replace the `sync-analyze` dispatch branch in `cli/__main__.py`**

Find the existing branch (it currently reads exactly):

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

Replace it with:

```python
    elif args.cmd == "sync-analyze":
        result = asyncio.run(_sync_analyze())
        p, c, pc = (result["propagation"], result["classification"],
                    result["port_candidates"])
        t = pc["tiers"]
        print(f"sync-analyze: groups={p['groups']} present={p['present']} "
              f"absent={p['absent']} cherry_links={p['cherry_links']} | "
              f"subsystems={c['total']} shared={c['shared']} expansion={c['expansion']} "
              f"mixed={c['mixed']} vendored={c['vendored']} | "
              f"port_candidates={pc['candidates']} "
              f"(surgical={t['surgical']} small={t['small']} moderate={t['moderate']} "
              f"bulk={t['bulk']}) skipped={pc['skipped_unportable']} "
              f"resolved={pc['auto_resolved']}")
```

- [ ] **Step 2: Run the full test suite**

Run: `cd /c/Users/Roko/Documents/PYTHON/MANGOS/mai && python -m pytest -q`
Expected: PASS — the prior suite (164) plus this plan's net-new tests (vendored cases, tier tests, vendored-exclusion, tier-distribution). Report the exact count (expected ~170).

- [ ] **Step 3: Offline smoke — tuned chain over a fixtured DB**

Write `mai-data/tmp/smoke_tuning.py`:

```python
import asyncio

from mai.db.base import Base
from mai.db.models import Commit, CommitFile, CommitPatch
from mai.db.session import SessionFactory, engine
from mai.sync.classify import classify_subsystems
from mai.sync.portcandidates import compute_port_candidates
from mai.sync.propagate import compute_propagation


async def _c(s, core, sha, patch_id, subsystem, added=3, removed=1):
    c = Commit(core=core, sha=sha, author="a", authored_at="t", committer="a",
               committed_at="t", message="m", parent_shas=["p"], is_merge=False)
    s.add(c)
    await s.flush()
    s.add(CommitPatch(commit_id=c.id, patch_id=patch_id))
    s.add(CommitFile(commit_id=c.id, path=f"{subsystem}/f.cpp", change_type="M",
                     added_lines=added, removed_lines=removed, subsystem=subsystem))


async def main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionFactory() as s:
        await _c(s, "three", "s1", "P1", "src/shared/Log", added=2, removed=0)  # surgical shared
        await _c(s, "two", "s2", "PV", "dep/zlib", added=9000, removed=0)        # vendored, excluded
        await s.commit()
        await compute_propagation(s)
        await classify_subsystems(s)
        print(await compute_port_candidates(s))


asyncio.run(main())
```

Run: `cd /c/Users/Roko/Documents/PYTHON/MANGOS/mai && rm -f mai.db && python mai-data/tmp/smoke_tuning.py`
Expected: prints `{'candidates': 1, 'skipped_unportable': 1, 'auto_resolved': 0, 'tiers': {'surgical': 1, 'small': 0, 'moderate': 0, 'bulk': 0}}` (the shared fix surfaces as surgical; the dep/zlib fix is vendored → skipped). Then `rm -f mai.db`.

- [ ] **Step 4: Commit**

```bash
git add mai/src/mai/cli/__main__.py
git commit -m "feat: sync-analyze surfaces vendored count + port-candidate tier breakdown"
```

---

## Self-Review

- **Goal coverage:** `vendored` class for `dep/` (Task 1) — removes the structural third-party-lib noise the real run exposed; `src/tools` deliberately kept `shared` ✓. Magnitude `tier` on every candidate (Task 2) + tier distribution in the pass + CLI (Tasks 3-4) — surgical fixes become filterable, bulk visibly demoted, nothing dropped ✓.
- **Invariants preserved:** port candidates still graduate ONLY on `classification == "shared"`, so vendored (like expansion/mixed) never produces port-debt — the load-bearing no-false-port-debt property is tightened, not weakened. Classifier stays conservative (`mixed` default unchanged). No silent caps — tiers label, never drop. Recompute idempotent; human `status` still preserved; tier re-stamps from magnitude on each upsert.
- **Placeholder scan:** none — every step has runnable code/commands + expected output.
- **Type consistency:** `magnitude_tier(int) -> str` defined in `repository/port_candidate.py`, imported there (upsert) and in `portcandidates.py` (distribution); `classify_subsystems` return gains `vendored` and the CLI reads `c['vendored']`; `compute_port_candidates` return gains `tiers` (dict with surgical/small/moderate/bulk) and the CLI reads `pc['tiers']`. All call sites updated in the same plan.

## Notes for later

- **Re-run on real forks** after this lands to confirm the dep noise is gone and the surgical tier dominates (re-use `mai-data/tmp/real_run.py`; it'll now show `vendored=` and tier bands). Expect ~229→~190 candidates with the dep 39 reclassified, and the surgical tier (≈111) leading.
- **2d squash-match** (PR-aggregate patch-id) remains the recall lever — the real run's `cherry_links≈0` confirmed mangos ports via PR squash/merge, so per-commit patch-id misses squash-merged ports.
- **`src/tools` format-magic divergence:** large extractor candidates (now tier `moderate`/`bulk`) are still surfaced; if they prove noisy, revisit — but small extractor fixes are genuine port-debt, so keep `src/tools` shared.
- **Migrations:** the new `tier` column still relies on `Base.metadata.create_all`; the Postgres/Neon deploy plan introduces Alembic + a baseline.
