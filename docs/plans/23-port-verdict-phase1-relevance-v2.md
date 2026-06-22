# Port-Verdict Phase 1 — Relevance v2 (client_bound) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Teach the subsystem classifier the **`client_bound`** relevance class (packet/opcode/protocol code that's divergent-by-design), both from path heuristics and from the drift "fully-diverged-across-all-pairs" signal — the foundation the later verdict gate uses to never recommend porting client-bound code.

**Architecture:** Extend the pure `classify_subsystem(path)->str` with a `client_bound` bucket (checked before `shared`, after `vendored`), and add an offline `seed_client_bound_from_drift(session)` that promotes fully-diverged `mixed` subsystems to `client_bound` (source `drift`), respecting manual overrides. Both are additive and offline; no schema change (the `classification`/`source` columns already store free strings).

**Tech Stack:** Python 3.12, async SQLAlchemy 2.0, pytest. No new dependencies.

## Global Constraints

- This is **Phase 1 of the Port-Verdict Engine** (`docs/specs/port-verdict-engine.md`). It only adds the relevance class; the git-apply test and the verdict stage are Phases 2–3.
- **`client_bound` is divergent-by-design** — it must never be treated as portable. (The downstream gate is Phase 3; here we only produce the classification.)
- **Conservative & non-breaking:** the path seeds must NOT reclassify anything the existing tests assert. In particular `src/game/Server` stays `mixed` via the path heuristic (the drift signal upgrades it, not the path list) — so `"server"` is deliberately NOT a client-bound path segment.
- **Manual overrides always win** (existing `upsert_auto` contract); the drift seed only upgrades subsystems the path heuristic left `mixed`.
- 4-space indent. `feat:` commits, **NO AI attribution** (no `Co-Authored-By`, no "Generated with"). Commit with `git -c user.name="r-log" commit -m "..."`.
- Follow existing patterns: classifier in `src/mai/sync/classify.py`; repo `SubsystemClassRepository.upsert_auto(subsystem, classification, source=...)` returns `True` if written, `False` if a `manual_override` was preserved. `DriftObservation` has `subsystem, shared, diverged, identical, only_a, only_b`. The `session` pytest fixture gives an in-memory async session with all tables. Run `python -m pytest`.

---

## File Structure

| File | Responsibility |
|------|----------------|
| `src/mai/sync/classify.py` (modify) | Add `CLIENT_BOUND_SEGMENTS` + `client_bound` branch in `classify_subsystem`; add `seed_client_bound_from_drift`; call it from `classify_subsystems` and report counts. |
| `src/mai/cli/__main__.py` (modify) | Surface `client_bound` in the `sync-analyze` print line. |
| `tests/test_classify.py` (modify) | Add `client_bound` path cases + ordering. |
| `tests/test_classify_drift_seed.py` (create) | The drift-signal seeding. |
| `tests/test_classify_run.py` (modify) | Assert the run reports `client_bound` and runs the drift seed. |

---

## Task 1: `client_bound` path classification

**Files:**
- Modify: `src/mai/sync/classify.py`
- Test: `tests/test_classify.py`

**Interfaces:**
- Produces: `classify_subsystem(subsystem)` now returns `"client_bound"` for paths containing a protocol/packet segment; `CLIENT_BOUND_SEGMENTS` constant. Existing return values unchanged for existing inputs.

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_classify.py`:

```python
@pytest.mark.parametrize("subsystem", [
    "src/game/Server/WorldHandlers",
    "src/game/Opcodes",
    "src/shared/Packets",          # client-bound beats shared
    "src/realmd/AuthSocket",       # client-bound beats shared
    "src/game/Server/Protocol",
])
def test_client_bound(subsystem):
    assert classify_subsystem(subsystem) == "client_bound"


def test_client_bound_beats_shared_and_expansion():
    # a packet-layout file under a shared prefix is client-bound, not shared
    assert classify_subsystem("src/shared/SMSG") == "client_bound"
    # vendored still wins over client_bound
    assert classify_subsystem("dep/foo/packets") == "vendored"


def test_server_dir_itself_stays_mixed():
    # 'server' alone is NOT a client-bound segment; the drift signal upgrades it
    assert classify_subsystem("src/game/Server") == "mixed"
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_classify.py -v`
Expected: the new `test_client_bound*` cases FAIL (currently return `mixed`/`shared`); `test_server_dir_itself_stays_mixed` already PASSES.

- [ ] **Step 3: Implement the `client_bound` branch**

In `src/mai/sync/classify.py`, add this constant next to `EXPANSION_SEGMENTS`:

```python
# Client/protocol-bound: byte layouts differ per WoW client build (15595 vs 12340 ...),
# so these are divergent-by-design and never cross-portable, even when text merges.
# NOTE: 'server' is intentionally absent — 'src/game/Server' stays 'mixed' via paths and
# is upgraded to client_bound only when the drift signal proves it fully diverged.
CLIENT_BOUND_SEGMENTS = frozenset({
    "worldhandlers", "opcode", "opcodes", "packet", "packets",
    "protocol", "smsg", "cmsg", "authsocket", "worldsocket",
})
```

Then change `classify_subsystem` so it checks `client_bound` after `vendored` and before `shared`:

```python
def classify_subsystem(subsystem: str) -> str:
    """Return 'vendored' | 'client_bound' | 'shared' | 'expansion' | 'mixed'.

    Conservative: vendored for third-party deps; client_bound for protocol/packet
    paths (divergent-by-design per client build); shared only for infrastructure
    prefixes; expansion when a path segment names version-bound content; else mixed.
    """
    s = subsystem.lower()
    for prefix in VENDORED_PREFIXES:
        if s == prefix or s.startswith(prefix + "/"):
            return "vendored"
    segments = s.split("/")
    if any(seg in CLIENT_BOUND_SEGMENTS for seg in segments):
        return "client_bound"
    for prefix in SHARED_PREFIXES:
        if s == prefix or s.startswith(prefix + "/"):
            return "shared"
    if any(seg in EXPANSION_SEGMENTS for seg in segments):
        return "expansion"
    return "mixed"
```

(Update the module docstring/return list if it enumerates the classes.)

- [ ] **Step 4: Run to verify they pass (and nothing regressed)**

Run: `python -m pytest tests/test_classify.py -v`
Expected: all PASS — the existing `shared`/`expansion`/`mixed`/`vendored` cases are unaffected (none of their paths contain a client-bound segment).

- [ ] **Step 5: Commit**

```bash
git add src/mai/sync/classify.py tests/test_classify.py
git -c user.name="r-log" commit -m "feat: client_bound subsystem class (packet/opcode paths divergent-by-design)"
```

---

## Task 2: drift-signal seeding + run wiring

**Files:**
- Modify: `src/mai/sync/classify.py`
- Modify: `src/mai/cli/__main__.py`
- Test: `tests/test_classify_drift_seed.py`
- Test: `tests/test_classify_run.py`

**Interfaces:**
- Consumes: `DriftObservation`, `SubsystemClassRepository` (Task 1's classifier).
- Produces: `async seed_client_bound_from_drift(session) -> int` (promotes fully-diverged `mixed` subsystems → `client_bound`, source `drift`); `classify_subsystems` now calls it and its return dict gains `"client_bound"` and `"client_bound_from_drift"`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_classify_drift_seed.py`:

```python
from mai.db.models import DriftObservation, SubsystemClass
from mai.repository.subsystem_class import SubsystemClassRepository
from mai.sync.classify import seed_client_bound_from_drift


async def _drift(session, sub, fork_b, *, identical, diverged):
    session.add(DriftObservation(fork_a="zero", fork_b=fork_b, subsystem=sub,
                                 shared=identical + diverged, diverged=diverged,
                                 identical=identical, only_a=0, only_b=0))


async def _mixed(session, sub):
    session.add(SubsystemClass(subsystem=sub, classification="mixed", source="heuristic"))


async def test_fully_diverged_mixed_becomes_client_bound(session):
    await _mixed(session, "src/game/Server")
    await _drift(session, "src/game/Server", "one", identical=0, diverged=9)
    await _drift(session, "src/game/Server", "two", identical=0, diverged=7)
    await session.commit()
    n = await seed_client_bound_from_drift(session)
    assert n == 1
    assert (await SubsystemClassRepository(session).get("src/game/Server")
            ).classification == "client_bound"


async def test_partly_identical_is_not_upgraded(session):
    await _mixed(session, "src/game/Maps")
    await _drift(session, "src/game/Maps", "one", identical=0, diverged=4)
    await _drift(session, "src/game/Maps", "two", identical=3, diverged=1)  # matches in one pair
    await session.commit()
    n = await seed_client_bound_from_drift(session)
    assert n == 0
    assert (await SubsystemClassRepository(session).get("src/game/Maps")
            ).classification == "mixed"


async def test_does_not_override_confident_or_manual(session):
    # a path-confident 'shared' subsystem that happens to be fully diverged is NOT demoted
    session.add(SubsystemClass(subsystem="src/shared/Foo", classification="shared",
                               source="heuristic"))
    session.add(SubsystemClass(subsystem="src/game/Bar", classification="mixed",
                               source="manual_override"))
    await _drift(session, "src/shared/Foo", "one", identical=0, diverged=5)
    await _drift(session, "src/game/Bar", "one", identical=0, diverged=5)
    await session.commit()
    n = await seed_client_bound_from_drift(session)
    assert n == 0
    repo = SubsystemClassRepository(session)
    assert (await repo.get("src/shared/Foo")).classification == "shared"
    assert (await repo.get("src/game/Bar")).classification == "mixed"
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_classify_drift_seed.py -v`
Expected: FAIL with `ImportError: cannot import name 'seed_client_bound_from_drift'`.

- [ ] **Step 3: Implement the drift seed**

In `src/mai/sync/classify.py`, add these imports at the top (alongside the existing ones):

```python
from collections import defaultdict

from mai.db.models import CommitFile, DriftObservation
```

(`CommitFile` is already imported; add `DriftObservation` and `defaultdict`.)

Add the function:

```python
async def seed_client_bound_from_drift(session) -> int:
    """Upgrade fully-diverged 'mixed' subsystems to 'client_bound' (source 'drift').

    A subsystem is 'fully diverged' when every drift observation of it has
    identical == 0 and diverged > 0 (nothing matches across any fork pair) — the
    fingerprint of client/protocol-bound code (e.g. WorldHandlers/Server). Only
    upgrades subsystems the path heuristic left 'mixed'; never overrides a confident
    path class or a manual_override.
    """
    obs = list(await session.scalars(select(DriftObservation)))
    by_sub: dict[str, list[DriftObservation]] = defaultdict(list)
    for o in obs:
        by_sub[o.subsystem].append(o)

    repo = SubsystemClassRepository(session)
    seeded = 0
    for sub, rows in by_sub.items():
        if not all(r.identical == 0 and r.diverged > 0 for r in rows):
            continue
        current = await repo.get(sub)
        if current is None or current.classification != "mixed":
            continue  # only upgrade an unknown ('mixed') auto classification
        if await repo.upsert_auto(sub, "client_bound", source="drift"):
            seeded += 1
    await session.commit()
    return seeded
```

- [ ] **Step 4: Run the drift-seed tests**

Run: `python -m pytest tests/test_classify_drift_seed.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Wire into `classify_subsystems` + report counts**

In `classify_subsystems`, add `"client_bound": 0` to the initial `counts` dict (so a path-classified `client_bound` increments cleanly), and after the existing `await session.commit()` (before `return counts`), call the drift seed and record it:

```python
    drift_seeded = await seed_client_bound_from_drift(session)
    counts["client_bound_from_drift"] = drift_seeded
    return counts
```

- [ ] **Step 6: Surface it in the CLI**

In `src/mai/cli/__main__.py`, in the `sync-analyze` dispatch branch, extend the classification part of the print to include client_bound. Find the `f"subsystems={c['total']} shared={c['shared']} expansion={c['expansion']} "` line and add `client_bound={c['client_bound']}(+{c.get('client_bound_from_drift', 0)} drift)` to it, e.g.:

```python
              f"subsystems={c['total']} shared={c['shared']} "
              f"client_bound={c['client_bound']}(+{c.get('client_bound_from_drift', 0)} drift) "
              f"expansion={c['expansion']} mixed={c['mixed']} vendored={c['vendored']} | "
```

- [ ] **Step 7: Update the run test**

In `tests/test_classify_run.py`, add a test that the run reports the new keys and that a fully-diverged mixed subsystem is upgraded end-to-end:

```python
async def test_run_reports_client_bound_and_applies_drift_seed(session):
    from mai.db.models import DriftObservation
    await _file(session, "src/game/Server/WorldHandlers",
                "src/game/Server/WorldHandlers/Misc.cpp")        # path -> client_bound
    await _file(session, "src/game/Server", "src/game/Server/WorldSocket.cpp")  # path -> mixed
    session.add(DriftObservation(fork_a="zero", fork_b="one",
                                 subsystem="src/game/Server", shared=5, diverged=5,
                                 identical=0, only_a=0, only_b=0))
    await session.commit()
    result = await classify_subsystems(session)
    assert result["client_bound"] >= 1                 # the WorldHandlers path
    assert result["client_bound_from_drift"] == 1      # Server upgraded by drift
    repo = SubsystemClassRepository(session)
    assert (await repo.get("src/game/Server")).classification == "client_bound"
```

Confirm the EXISTING `test_classifies_distinct_subsystems` still passes (no drift rows there → `client_bound_from_drift == 0`; its asserted keys are unaffected by the added ones).

- [ ] **Step 8: Run the suite**

Run: `python -m pytest tests/test_classify.py tests/test_classify_drift_seed.py tests/test_classify_run.py -v`
Expected: all PASS.
Run: `python -m pytest -q`
Expected: full suite green (the added classes don't change current port-candidate behavior — `client_bound` is not `shared`, so it doesn't graduate, exactly like `mixed` didn't).

- [ ] **Step 9: Commit**

```bash
git add src/mai/sync/classify.py src/mai/cli/__main__.py tests/test_classify_drift_seed.py tests/test_classify_run.py
git -c user.name="r-log" commit -m "feat: seed client_bound from drift fully-diverged signal"
```

---

## Self-Review

**Spec coverage (`port-verdict-engine.md`, Phase 1 / §6.1):**
- "`client_bound` class (paths)" → Task 1.
- "drift fully-diverged auto-seed" → Task 2 `seed_client_bound_from_drift`.
- "manual_override always wins; drift only upgrades `mixed`" → Task 2 guard (`classification != "mixed"` skip; `upsert_auto` preserves manual).
- "portable ⇔ shared; divergent ⇔ expansion|client_bound|vendored" → the *consumption* of this is Phase 3 (the verdict gate); Phase 1 only produces the class. Correctly out of scope here.

**Deliberately deferred (not gaps):** the git-apply test (Phase 2), the `PortVerdict` verdict stage that *uses* `client_bound` to bar NEEDS (Phase 3), the board re-model (Phase 4). Phase 1 is intentionally inert on current behavior — it adds a label nothing gates on yet.

**Non-breaking check:** existing `test_classify.py` paths contain no client-bound segment, so `shared/expansion/mixed/vendored` results are unchanged; `src/game/Server` stays `mixed` by path (asserted) and is upgraded only by the drift signal. `test_classifies_distinct_subsystems` asserts specific count keys; the added `client_bound`/`client_bound_from_drift` keys (0 there) don't break it.

**Placeholder scan:** none — every step has complete code. The exact `CLIENT_BOUND_SEGMENTS` list is a conservative seed flagged in the spec (§14 #1) for maintainer confirmation; it's overridable and additive, not a placeholder.

**Type consistency:** `classify_subsystem(str) -> str` and `seed_client_bound_from_drift(session) -> int` are used identically across tasks; `upsert_auto(subsystem, classification, source=...) -> bool` matches the existing repo signature; `counts` keys `client_bound`/`client_bound_from_drift` are written in Task 2 and read by the CLI + run test.

---

## Execution Handoff

After saving, choose execution mode:
1. **Subagent-Driven (recommended)** — a fresh subagent per task, two-stage review between tasks.
2. **Inline Execution** — execute in this session with checkpoints.
