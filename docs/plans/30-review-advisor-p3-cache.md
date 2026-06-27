# Review Advisor P3 — L1 Verdict Cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Memoize the grounded review opinion in a `ReviewAdvice` cache so the LLM is called at most once per `(patch_group_id, core, source_sha, base_sha, model, prompt_version)`; every later expand returns the stored opinion with no billed call.

**Architecture:** A new `ReviewAdvice` cache table (cache-only, drop/rebuild-safe). `build_review_advice` computes the current key, looks up the row, returns it on an exact-key hit (no judge call), else computes + grounds + upserts. Auto-invalidates when any validity key changes; judge failures are never cached. Phase 1 of `docs/specs/memory-hierarchy-design.md`.

**Tech Stack:** Python 3.12, async SQLAlchemy 2.0, pytest (asyncio_mode=auto). No new dependencies.

## Global Constraints

- **Derived & recomputable** — the cache is keyed on its inputs and never authoritative. Validity keys: `source_sha`, `base_sha` (= `git_client.head_sha(core)`), `model` (= `choose_model(evidence, settings)`), `prompt_version` (= `PROMPT_VERSION`).
- **Cache only successful opinions** — when the judge raises / returns null, return `opinion=None` and write NOTHING (the next expand retries).
- **Invariant 1 preserved** — non-review items (`build_review_evidence` returns None) take the no-judge, no-cache path unchanged.
- **API + UI unchanged** — the `opinion` dict shape is identical (`assessment, confidence, reason, tips, citations, adapted_hunks`), so `/api/review` and `renderAdvice` are untouched.
- **New table via create_all** — `Base.metadata.create_all` creates the new table; for the live `mai.db`, a one-time `mai-data/tmp/` create script (create_all is idempotent — only creates missing tables, never alters).
- No AI attribution in commits; conventional-commit style; 4-space indent.

---

## File Structure

| File | Change | Responsibility |
|------|--------|----------------|
| `src/mai/db/models.py` | Modify | add `ReviewAdvice` model (+ `Float`, `Boolean` imports if missing) |
| `src/mai/sync/review.py` | Modify | cache lookup/upsert in `build_review_advice` + `_opinion_from_row` helper |
| `mai-data/tmp/create_review_advice.py` | Create | one-time table create against the live `mai.db` (gitignored) |
| `tests/test_review_advice_cache.py` | Create | miss→write, hit→no-judge, key-change→recompute, failure→not-cached |

---

### Task 1: `ReviewAdvice` cache model

**Files:**
- Modify: `src/mai/db/models.py`
- Create: `mai-data/tmp/create_review_advice.py`
- Test: `tests/test_review_advice_cache.py` (model-creation portion)

**Interfaces:**
- Produces: `ReviewAdvice` ORM model, table `review_advice`, unique `(patch_group_id, core)`, columns: `source_sha`, `base_sha`, `model`, `prompt_version`, `assessment`, `confidence`, `reason`, `tips`, `citations`, `adapted_hunks`, `grounded`, `computed_at`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_review_advice_cache.py
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mai.db.base import Base
from mai.db.models import ReviewAdvice


@pytest_asyncio.fixture
async def cache_session():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(eng, expire_on_commit=False)() as s:
        yield s


async def test_review_advice_row_roundtrips(cache_session):
    cache_session.add(ReviewAdvice(
        patch_group_id="pg1", core="four", source_sha="s1", base_sha="b1",
        model="anthropic/claude-sonnet-4.6", prompt_version=1, assessment="divergent",
        confidence=0.6, reason="x", tips=["t"], citations=["c"], adapted_hunks=[], grounded=True))
    await cache_session.commit()
    row = await cache_session.scalar(select(ReviewAdvice).where(
        ReviewAdvice.patch_group_id == "pg1", ReviewAdvice.core == "four"))
    assert row.assessment == "divergent" and row.confidence == 0.6
    assert row.tips == ["t"] and row.grounded is True
```

- [ ] **Step 2: Run it, expect failure** — `python -m pytest tests/test_review_advice_cache.py -q` → FAIL (`ReviewAdvice` undefined).

- [ ] **Step 3: Implement the model** — in `src/mai/db/models.py`, first ensure `Float` and `Boolean` are imported from `sqlalchemy` (read the existing `from sqlalchemy import (...)` block; add `Float, Boolean` if absent). Then add, beside `PortVerdict` (reuse the same `_uuid`/`_now` module helpers and `JSON`/`String`/`Integer`/`Text` already in use):

```python
class ReviewAdvice(Base):
    """Cache of the grounded review opinion for one (fix, core). Derived & recomputable:
    keyed on (source_sha, base_sha, model, prompt_version); recomputed when any changes.
    Cache-only — safe to drop/rebuild."""
    __tablename__ = "review_advice"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    patch_group_id: Mapped[str] = mapped_column(String(36))
    core: Mapped[str] = mapped_column(String(64))
    source_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    base_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    model: Mapped[str] = mapped_column(String(128))
    prompt_version: Mapped[int] = mapped_column(Integer, default=0)
    assessment: Mapped[str] = mapped_column(String(20))
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    reason: Mapped[str] = mapped_column(Text, default="")
    tips: Mapped[list] = mapped_column(JSON, default=list)
    citations: Mapped[list] = mapped_column(JSON, default=list)
    adapted_hunks: Mapped[list] = mapped_column(JSON, default=list)
    grounded: Mapped[bool] = mapped_column(Boolean, default=True)
    computed_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)

    __table_args__ = (
        UniqueConstraint("patch_group_id", "core", name="uq_review_advice"),
    )
```

- [ ] **Step 4: Run test, expect pass** — `python -m pytest tests/test_review_advice_cache.py -q` → PASS.

- [ ] **Step 5: Create the live-db migration helper** `mai-data/tmp/create_review_advice.py`:

```python
"""Create the new review_advice cache table on the live mai.db. create_all is
idempotent — it only creates missing tables, never alters/drops existing ones."""
import asyncio
from mai.db.base import Base
from mai.db.session import engine
import mai.db.models  # noqa: F401  (register models on Base.metadata)


async def main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("OK: ensured review_advice (and any other missing tables) exist")


asyncio.run(main())
```

> Do NOT run it as part of the test cycle; it is an ops step the controller runs once against the live DB after the code lands.

- [ ] **Step 6: Run the full suite** — `python -m pytest -q` → green.

- [ ] **Step 7: Commit**

```bash
git add src/mai/db/models.py tests/test_review_advice_cache.py mai-data/tmp/create_review_advice.py
git commit -m "feat: ReviewAdvice cache table (derived, recomputable)"
```

---

### Task 2: cache read/write in `build_review_advice`

**Files:**
- Modify: `src/mai/sync/review.py`
- Test: `tests/test_review_advice_cache.py`

**Interfaces:**
- Consumes: `ReviewAdvice` (Task 1), `build_review_evidence`, `choose_model`, `ground_opinion`, `git_client.head_sha`, `PROMPT_VERSION` (`mai.judge.prompt`).
- Produces: `build_review_advice` now returns the cached opinion on an exact-key hit (no judge call); on miss computes, grounds, and upserts; on judge failure returns `opinion=None` and writes nothing. Signature and return shape unchanged: `{"evidence": ..., "opinion": <dict|None>}`.

- [ ] **Step 1: Write the failing tests** — reuse the seeded-review fixture shape from `tests/test_review_evidence.py` (PatchGroup + Commit + CommitFile + a `review` PortVerdict; `FakeGitClient` with `_diffs`/`_rejected`/`_regions`/`_logs`; the cache also reads `head_sha`, default `f"head-{core}"`). READ that file and mirror its `session` fixture.

```python
# add to tests/test_review_advice_cache.py
from datetime import datetime, timezone
from sqlalchemy import func
from mai.db.models import PatchGroup, Commit, CommitFile, PortVerdict
from mai.git.fake import FakeGitClient
from mai.judge.fake import FakeJudge
from mai.judge.schema import ReviewOpinion
from mai.sync.review import build_review_advice

PATCH = ("diff --git a/src/shared/Db.cpp b/src/shared/Db.cpp\n"
         "--- a/src/shared/Db.cpp\n+++ b/src/shared/Db.cpp\n"
         "@@ -1,3 +1,4 @@\n a\n b\n+x\n c\n")


@pytest_asyncio.fixture
async def seeded(cache_session):
    s = cache_session
    s.add(PatchGroup(id="pg1", patch_id="p1"))
    ts = datetime(2026, 6, 1, tzinfo=timezone.utc)
    cm = Commit(core="three", sha="sha123abcd", author="a", authored_at=ts,
                committer="a", committed_at=ts, message="db crash fix on shutdown")
    s.add(cm)
    await s.flush()
    s.add(CommitFile(commit_id=cm.id, path="src/shared/Db.cpp", subsystem="src/shared",
                     change_type="M", added_lines=2, removed_lines=1))
    s.add(PortVerdict(patch_group_id="pg1", core="four", verdict="review",
                      apply_result="conflict", relevance="portable", source_core="three",
                      source_sha="sha123abcd", subsystem="src/shared", magnitude=3,
                      tier="surgical", conflict_applied=1, conflict_total=1))
    await s.commit()
    return s


def _git():
    g = FakeGitClient()
    g._diffs = {("three", "sha123abcd"): PATCH}
    g._rejected = {("four", PATCH): {}}
    g._logs = {"four": []}
    g._heads = {"four": "base1"}
    return g


def _judge():
    return FakeJudge(opinion=ReviewOpinion(assessment="divergent", confidence=0.6,
                                           reason="differs"))


async def test_cache_miss_then_hit_skips_second_judge_call(seeded):
    git, judge = _git(), _judge()
    out1 = await build_review_advice(seeded, git, judge, "pg1:four")
    assert out1["opinion"]["assessment"] == "divergent"
    assert judge.calls == 1
    n = await seeded.scalar(func.count().select() if False else
                            __import__("sqlalchemy").select(func.count()).select_from(ReviewAdvice))
    assert n == 1                                   # row written
    out2 = await build_review_advice(seeded, git, judge, "pg1:four")
    assert out2["opinion"]["assessment"] == "divergent"
    assert judge.calls == 1                         # HIT: judge NOT called again


async def test_cache_invalidates_when_base_sha_moves(seeded):
    git, judge = _git(), _judge()
    await build_review_advice(seeded, git, judge, "pg1:four")
    assert judge.calls == 1
    git._heads["four"] = "base2"                    # target HEAD moved
    await build_review_advice(seeded, git, judge, "pg1:four")
    assert judge.calls == 2                          # MISS: recomputed


async def test_judge_failure_is_not_cached(seeded):
    git = _git()
    failing = FakeJudge(raises=RuntimeError("boom"))
    out = await build_review_advice(seeded, git, failing, "pg1:four")
    assert out["opinion"] is None
    n = await seeded.scalar(__import__("sqlalchemy").select(func.count()).select_from(ReviewAdvice))
    assert n == 0                                    # nothing cached
    ok = _judge()
    out2 = await build_review_advice(seeded, git, ok, "pg1:four")
    assert out2["opinion"]["assessment"] == "divergent" and ok.calls == 1
```

> The `func.count()` lines above are intentionally explicit; if your linter dislikes the inline `__import__`, add `from sqlalchemy import select, func` to the test imports and use `select(func.count()).select_from(ReviewAdvice)`. Match the project's existing test import style.

- [ ] **Step 2: Run them, expect failure** — `python -m pytest tests/test_review_advice_cache.py -q` → the cache tests FAIL (no caching yet: `judge.calls == 2` on the hit test, row not written).

- [ ] **Step 3: Implement** — in `src/mai/sync/review.py`, add imports at the top (beside the existing ones):

```python
from mai.db.models import ReviewAdvice
from mai.judge.prompt import PROMPT_VERSION
```

(`select`, `ground_opinion`, `choose_model`, and `settings as _settings` are already imported.) Add the helper and rewrite `build_review_advice`:

```python
def _opinion_from_row(row: ReviewAdvice) -> dict:
    return {"assessment": row.assessment, "confidence": row.confidence,
            "reason": row.reason, "tips": row.tips, "citations": row.citations,
            "adapted_hunks": row.adapted_hunks}


async def build_review_advice(session, git_client, judge, item_id, *, settings=_settings):
    """Collect evidence (P1); for a review item with a judge, return the cached grounded
    opinion on an exact-key hit, else compute -> ground -> upsert. Judge failures are not
    cached. Invariant 1: non-review -> evidence None, no judge, no cache."""
    evidence = await build_review_evidence(session, git_client, item_id)
    if evidence is None or judge is None:
        return {"evidence": evidence, "opinion": None}

    pg_id, _, core = item_id.rpartition(":")
    source_sha = (evidence.get("fix") or {}).get("source_sha")
    base_sha = await git_client.head_sha(core)
    model = choose_model(evidence, settings)

    row = await session.scalar(select(ReviewAdvice).where(
        ReviewAdvice.patch_group_id == pg_id, ReviewAdvice.core == core))
    if (row is not None and row.source_sha == source_sha and row.base_sha == base_sha
            and row.model == model and row.prompt_version == PROMPT_VERSION):
        return {"evidence": evidence, "opinion": _opinion_from_row(row)}   # cache hit

    try:
        opinion = ground_opinion(await judge.judge(evidence, model), evidence).model_dump()
    except Exception:  # noqa: BLE001 — a judge/network/schema failure must never 500
        return {"evidence": evidence, "opinion": None}   # do NOT cache failures

    if row is None:
        row = ReviewAdvice(patch_group_id=pg_id, core=core)
        session.add(row)
    row.source_sha = source_sha
    row.base_sha = base_sha
    row.model = model
    row.prompt_version = PROMPT_VERSION
    row.assessment = opinion["assessment"]
    row.confidence = opinion["confidence"]
    row.reason = opinion["reason"]
    row.tips = opinion["tips"]
    row.citations = opinion["citations"]
    row.adapted_hunks = opinion["adapted_hunks"]
    row.grounded = True
    await session.commit()
    return {"evidence": evidence, "opinion": opinion}
```

- [ ] **Step 4: Run the cache tests + full suite** — `python -m pytest tests/test_review_advice_cache.py -q` → PASS, then `python -m pytest -q` → green (the existing `tests/test_review_advice.py` and `tests/test_review_api.py` still pass — their `FakeGitClient.head_sha` defaults work, and the FakeJudge path now also writes a cache row, which those tests don't assert against).

- [ ] **Step 5: Commit**

```bash
git add src/mai/sync/review.py tests/test_review_advice_cache.py
git commit -m "feat: L1 cache in build_review_advice — judge at most once per key"
```

---

## Self-Review

- **Spec coverage (`memory-hierarchy-design.md` Phase 1):** model (Task 1) · read/write flow with the 4 validity keys (Task 2) · cache-only-success + Invariant 1 (Task 2) · migration helper (Task 1 Step 5) · all five test cases (Tasks 1–2). ✅
- **Key correctness:** the validity comparison checks all four keys (`source_sha`, `base_sha`, `model`, `prompt_version`); a change in any → miss → recompute + overwrite (tested for `base_sha`). ✅
- **Cache-only-success:** the `except` returns before any `session.add`/commit. ✅
- **API/UI untouched:** `_opinion_from_row` reconstructs exactly the `model_dump()` keys the route already serializes and `renderAdvice` reads. ✅
- **Placeholder scan:** none — the only out-of-cycle step is the live-DB create (Task 1 Step 5), explicitly an ops step.
- **Type consistency:** `_opinion_from_row` keys == `ReviewOpinion.model_dump()` keys == row columns == what Task 2's upsert writes. ✅

## Execution Handoff

Recommended: **subagent-driven-development** (fresh implementer + task review per task, final whole-branch review), same as P1/P2.
