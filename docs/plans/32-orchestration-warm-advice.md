# Orchestration Phase 1 — Warm-Advice Batch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A bounded, concurrent `warm_advice` batch that pre-computes advisor opinions over the near/partial review backlog (reusing `build_review_advice` + the L1 cache), exposed as a `mai warm-advice` CLI command — so the board's "Worth reviewing" rows show opinions instantly and free.

**Architecture:** `_warm_plan` builds the prioritized work-list (near→partial review items without a cache row, capped) in one session; `warm_advice` fans out `build_review_advice` per item, each in its OWN session, under an `asyncio.Semaphore` (LLM calls overlap; each worker commits its own cache row — resumable). Mirrors `enrich_pending_concurrent`. Phase 1 of `docs/specs/orchestration-core-design.md`.

**Tech Stack:** Python 3.12, asyncio, async SQLAlchemy, pytest. No new deps.

## Global Constraints

- **Bounded** — at most `limit` items/run; only `near`+`partial` bands (conflict_total truthy); never the far tail.
- **Same model as on-demand** — `warm_advice` calls `build_review_advice` with the real judge, so warmed rows are the exact rows an on-demand expand reads (a true L1 cache hit; instant + free). No cheap-model split.
- **Per-worker session** — each worker opens its own session from `session_factory`; the plan is built once up front in its own session. (One async session is not safe for concurrent use.)
- **Resumable & idempotent** — each item commits its own cache row (`build_review_advice` does this); a re-run's plan excludes already-cached items → converges to 0.
- **Best-effort** — a failed item is counted + skipped, never aborts the batch.
- **No new authority/cache** — warming only populates the existing `ReviewAdvice` cache.
- No AI attribution; conventional commits; 4-space indent.

---

## File Structure

| File | Change | Responsibility |
|------|--------|----------------|
| `src/mai/orchestrate/__init__.py` | Create | package marker |
| `src/mai/orchestrate/warm.py` | Create | `_warm_plan` + `warm_advice` |
| `src/mai/cli/__main__.py` | Modify | `warm-advice` subcommand + `_warm_advice` helper |
| `tests/test_warm_advice.py` | Create | plan + batch (temp-file sqlite, FakeJudge/FakeGitClient) |
| `tests/test_cli_parser_*.py` or new | Modify/Create | `warm-advice` arg parsing |

---

### Task 1: `warm_advice` batch

**Files:**
- Create: `src/mai/orchestrate/__init__.py`, `src/mai/orchestrate/warm.py`
- Test: `tests/test_warm_advice.py`

**Interfaces:**
- Consumes: `PortVerdict`, `ReviewAdvice`, `build_review_advice`, `closeness_label`.
- Produces: `async _warm_plan(session, limit) -> list[str]`; `async warm_advice(session_factory, git_client, judge, *, limit=200, concurrency=4) -> dict` returning `{"planned", "warmed", "failed"}`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_warm_advice.py
from datetime import datetime, timezone
import pytest_asyncio
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mai.db.base import Base
from mai.db.models import PatchGroup, Commit, CommitFile, PortVerdict, ReviewAdvice
from mai.git.fake import FakeGitClient
from mai.judge.fake import FakeJudge
from mai.judge.schema import ReviewOpinion
from mai.orchestrate.warm import _warm_plan, warm_advice

PATCH = ("diff --git a/src/shared/Db.cpp b/src/shared/Db.cpp\n"
         "--- a/src/shared/Db.cpp\n+++ b/src/shared/Db.cpp\n"
         "@@ -1,3 +1,4 @@\n a\n b\n+x\n c\n")


def _engine_factory(tmp_path):
    # temp-FILE sqlite so per-worker sessions share data (in-memory would not).
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/warm.db")
    return eng, async_sessionmaker(eng, expire_on_commit=False)


async def _seed_review(s, pg_id, core, sha, applied, total, *, with_advice=False):
    s.add(PatchGroup(id=pg_id, patch_id="p-" + pg_id))
    ts = datetime(2026, 6, 1, tzinfo=timezone.utc)
    cm = Commit(core="three", sha=sha, author="a", authored_at=ts, committer="a",
                committed_at=ts, message="db crash fix on shutdown")
    s.add(cm)
    await s.flush()
    s.add(CommitFile(commit_id=cm.id, path="src/shared/Db.cpp", subsystem="src/shared",
                     change_type="M", added_lines=2, removed_lines=1))
    s.add(PortVerdict(patch_group_id=pg_id, core=core, verdict="review",
                      apply_result="conflict", relevance="portable", source_core="three",
                      source_sha=sha, subsystem="src/shared", magnitude=3, tier="surgical",
                      conflict_applied=applied, conflict_total=total))
    if with_advice:
        s.add(ReviewAdvice(patch_group_id=pg_id, core=core, source_sha=sha, base_sha="b",
                           model="m", prompt_version=1, assessment="divergent",
                           confidence=0.5, reason="x"))


@pytest_asyncio.fixture
async def factory(tmp_path):
    eng, fac = _engine_factory(tmp_path)
    async with eng.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    async with fac() as s:
        await _seed_review(s, "pg_near", "four", "shaNEAR0001", 5, 5)        # near (1.0)
        await _seed_review(s, "pg_part", "four", "shaPART0001", 2, 4)        # partial (0.5)
        await _seed_review(s, "pg_far", "four", "shaFAR00001", 1, 5)         # far (0.2) -> excluded
        await _seed_review(s, "pg_cached", "four", "shaCACHED01", 5, 5, with_advice=True)  # skip
        await s.commit()
    return fac


def _git():
    g = FakeGitClient(files={})
    g._diffs = {("three", "shaNEAR0001"): PATCH, ("three", "shaPART0001"): PATCH}
    g._rejected = {("four", PATCH): {}}
    g._logs = {"four": []}
    g._heads = {"four": "baseX"}
    return g


def _judge():
    return FakeJudge(opinion=ReviewOpinion(assessment="divergent", confidence=0.5, reason="d"))


async def test_warm_plan_filters_prioritizes_and_skips(factory):
    async with factory() as s:
        plan = await _warm_plan(s, limit=10)
    # only near + partial, near first; far excluded; cached excluded
    assert plan == ["pg_near:four", "pg_part:four"]


async def test_warm_plan_respects_limit(factory):
    async with factory() as s:
        plan = await _warm_plan(s, limit=1)
    assert plan == ["pg_near:four"]            # near prioritized


async def test_warm_advice_warms_then_idempotent(factory):
    git, judge = _git(), _judge()
    r1 = await warm_advice(factory, git, judge, limit=10, concurrency=2)
    assert r1["planned"] == 2 and r1["warmed"] == 2 and r1["failed"] == 0
    async with factory() as s:
        n = await s.scalar(select(func.count()).select_from(ReviewAdvice))
    assert n == 3                              # 2 newly warmed + the 1 pre-seeded
    r2 = await warm_advice(factory, git, judge, limit=10, concurrency=2)
    assert r2["planned"] == 0 and r2["warmed"] == 0    # all cached now


async def test_warm_advice_failure_isolated(factory):
    git = _git()
    failing = FakeJudge(raises=RuntimeError("boom"))
    r = await warm_advice(factory, git, failing, limit=10, concurrency=2)
    assert r["planned"] == 2 and r["warmed"] == 0 and r["failed"] == 2   # both fail, no raise
```

- [ ] **Step 2: Run, expect failure** — `python -m pytest tests/test_warm_advice.py -q` → FAIL (module missing).

- [ ] **Step 3: Implement `src/mai/orchestrate/__init__.py`** (empty) and `src/mai/orchestrate/warm.py`:

```python
import asyncio

from sqlalchemy import select

from mai.db.models import PortVerdict, ReviewAdvice
from mai.sync.review import build_review_advice
from mai.sync.verdicts import closeness_label

_BAND_RANK = {"near": 0, "partial": 1}


async def _warm_plan(session, limit: int) -> list[str]:
    """Review items in the near/partial band with no advice row yet, near before
    partial, capped at `limit`. Returns item_ids ('{patch_group_id}:{core}')."""
    rows = (await session.scalars(
        select(PortVerdict).where(PortVerdict.verdict == "review"))).all()
    ranked: list[tuple[int, str]] = []
    for v in rows:
        if not v.conflict_total:
            continue
        band = closeness_label(v.conflict_applied or 0, v.conflict_total)
        if band not in _BAND_RANK:
            continue
        has_row = await session.scalar(select(ReviewAdvice.id).where(
            ReviewAdvice.patch_group_id == v.patch_group_id, ReviewAdvice.core == v.core))
        if has_row is not None:
            continue
        ranked.append((_BAND_RANK[band], f"{v.patch_group_id}:{v.core}"))
    ranked.sort(key=lambda t: t[0])
    return [item_id for _, item_id in ranked[:limit]]


async def warm_advice(session_factory, git_client, judge, *, limit: int = 200,
                      concurrency: int = 4) -> dict:
    """Pre-compute advisor opinions over the near/partial review backlog. Bounded,
    concurrent, resumable: each item runs build_review_advice in its OWN session
    (LLM calls overlap; each commits its own cache row). A failed item is skipped."""
    async with session_factory() as session:
        plan = await _warm_plan(session, limit)

    sem = asyncio.Semaphore(concurrency)
    lock = asyncio.Lock()
    counts = {"planned": len(plan), "warmed": 0, "failed": 0}

    async def worker(item_id: str) -> None:
        async with sem:
            try:
                async with session_factory() as s:
                    out = await build_review_advice(s, git_client, judge, item_id)
            except Exception:  # noqa: BLE001 — one bad item must not abort the batch
                async with lock:
                    counts["failed"] += 1
                return
        async with lock:
            if out.get("opinion") is not None:
                counts["warmed"] += 1
            else:
                counts["failed"] += 1

    await asyncio.gather(*(worker(i) for i in plan))
    return counts
```

- [ ] **Step 4: Run the tests + full suite** — `python -m pytest tests/test_warm_advice.py -q` → PASS, then `python -m pytest -q` → green.

- [ ] **Step 5: Commit**

```bash
git add src/mai/orchestrate/__init__.py src/mai/orchestrate/warm.py tests/test_warm_advice.py
git commit -m "feat: warm_advice batch — bounded concurrent advisor pre-pass over the review backlog"
```

---

### Task 2: `mai warm-advice` CLI command

**Files:**
- Modify: `src/mai/cli/__main__.py`
- Test: a CLI parser test (mirror `tests/test_cli_parser_b1.py` if present, else add `tests/test_warm_advice_cli.py`)

**Interfaces:**
- Consumes: `warm_advice` (Task 1), `OpenRouterJudge`, `LocalGitClient`, `SessionFactory`, `settings`.
- Produces: `mai warm-advice [--limit N] [--concurrency K]` → builds the judge from the key, runs `warm_advice`, prints `planned/warmed/failed`.

- [ ] **Step 1: Write the failing test** — read `tests/test_cli_parser_b1.py` (or however the project tests the argparse `build_parser`/`main`); add a case asserting `warm-advice --limit 50 --concurrency 2` parses with those values. If the CLI builds its parser inline in `main()` (no separate `build_parser`), add a minimal test that imports the module and asserts the subparser exists, OR assert via `argparse` that the args are accepted. Match the existing CLI-test style.

```python
# tests/test_warm_advice_cli.py  (adapt to the project's actual CLI-test pattern)
from mai.cli.__main__ import build_parser   # or the real parser entry; read the module first

def test_warm_advice_args_parse():
    args = build_parser().parse_args(["warm-advice", "--limit", "50", "--concurrency", "2"])
    assert args.cmd == "warm-advice" and args.limit == 50 and args.concurrency == 2
```

> If there is no `build_parser` to import, read `cli/__main__.py` and mirror whatever the existing parser tests do (e.g. `test_cli_parser_b1.py`); the assertion content (cmd/limit/concurrency) stays the same.

- [ ] **Step 2: Run, expect failure** — the subcommand doesn't exist yet.

- [ ] **Step 3: Implement** — in `src/mai/cli/__main__.py`:
  1. Add the subparser beside the others (e.g. near `sub.add_parser("sync-analyze")`):

```python
    wa = sub.add_parser("warm-advice")
    wa.add_argument("--limit", type=int, default=200)
    wa.add_argument("--concurrency", type=int, default=4)
```

  2. Add the async helper (near `_sync_analyze`):

```python
async def _warm_advice(limit: int, concurrency: int):
    from mai.db.session import SessionFactory
    from mai.git.client import LocalGitClient
    from mai.orchestrate.warm import warm_advice
    if not settings.openrouter_api_key:
        return None
    from mai.judge.judge import OpenRouterJudge
    judge = OpenRouterJudge(settings.openrouter_api_key, settings.openrouter_api_url)
    git = LocalGitClient(settings.git_mirror_dir, settings.git_worktree_dir)
    return await warm_advice(SessionFactory, git, judge, limit=limit, concurrency=concurrency)
```

  3. Add the dispatch branch (beside `sync-analyze`):

```python
    elif args.cmd == "warm-advice":
        result = asyncio.run(_warm_advice(args.limit, args.concurrency))
        if result is None:
            print("warm-advice: no OPENROUTER_API_KEY set — nothing warmed")
        else:
            print(f"warm-advice: planned={result['planned']} "
                  f"warmed={result['warmed']} failed={result['failed']}")
```

> Read the current `main()` to match how it constructs the parser (`argparse.ArgumentParser` + `add_subparsers(dest=...)`) and where `settings`/`asyncio` are imported. If the dest attribute isn't `cmd`, use the real one.

- [ ] **Step 4: Run the test + full suite** — both green. (Do NOT run the real `warm-advice` command here — it makes billed calls; the controller does a guarded live smoke separately.)

- [ ] **Step 5: Commit**

```bash
git add src/mai/cli/__main__.py tests/test_warm_advice_cli.py
git commit -m "feat: mai warm-advice CLI — bounded advisor backlog pre-pass"
```

---

## Self-Review

- **Spec coverage (`orchestration-core-design.md`):** `_warm_plan` filtering/priority/skip/cap (T1) · `warm_advice` per-worker-session bounded fan-out + best-effort + idempotent (T1) · CLI manual-trigger bounded (T2). ✅
- **Bounded/idempotent/best-effort/same-model/per-worker-session:** all in T1's `warm_advice` + tests (`test_warm_advice_warms_then_idempotent`, `test_warm_advice_failure_isolated`). ✅
- **Reuses L1:** worker calls `build_review_advice` (its own cache write/commit); no new cache. ✅
- **Type consistency:** `warm_advice` returns `{planned,warmed,failed}`; the CLI prints exactly those keys. ✅
- **Placeholder scan:** clean; CLI-test shape is the only "match the real pattern" note (the assertion content is fixed).

## Execution Handoff
Recommended: **subagent-driven-development**.
