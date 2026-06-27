---
title: "Mai — Orchestration Core (Sub-project B, Phase 1) — design"
status: Approved
version: 1.0
date: 2026-06-27
owners: [r-log]
related:
  - porting-agent-program.md
  - review-advisor-p2-design.md
  - memory-hierarchy-design.md
---

# Orchestration Core (Phase 1) — design

Sub-project B of the [[porting-agent-program]]: the "superpowers inside Mai" — `plan → fan-out →
per-item → (cache)`. **Phase 1 is the smallest real instance of that primitive**: a bounded,
concurrent **advisor batch pre-pass** that pre-warms the review backlog's opinions. It reuses Mai's
existing fan-out shape (`enrich_pending_concurrent`) and the L1 advisor cache — assembly, not a new
framework. Later B phases (model escalation, a verify lane, the porting-classification batch over
decomp commits × forks) extend the same primitive.

## 1. What exists to reuse

- **Fan-out primitive:** `enrich_pending_concurrent` (`enrich_run.py`) — build a plan single-threaded
  (skipping already-done), `asyncio.Semaphore`-bound concurrent model calls, commit-per-item
  (resumable). B copies this shape.
- **Per-item pipeline:** `build_review_advice` (P2/P3) already does evidence → judge → ground →
  cache, best-effort, with the L1 cache. The batch just calls it over many items.
- **Bands:** `closeness_label(conflict_applied, conflict_total)` (`sync/verdicts.py`) yields
  `near|partial|far`. The backlog UI shows `near`+`partial`.

## 2. Decisions (owner-approved)

- **Manual CLI, bounded per run.** A `warm-advice` command; each run warms up to `--limit` items.
  No auto-run in the refresh cycle (spending stays explicit).
- **Same model as on-demand.** The L1 cache is keyed on `model`; warming with the *same* model the
  on-demand expand routes to makes a warmed item a true cache hit on expand (instant + free for the
  reviewer). Cheap-model-first is deferred (it splits the cache key — its own later optimization).
- **Per-worker session.** Each worker opens its own session from a `session_factory` so LLM calls
  genuinely overlap (one async session is not safe for concurrent use). The plan is built once,
  single-session, before fan-out.
- **near before partial; never the far tail.** Priority = `near`, then `partial`. `far`/unbanded
  review items are excluded (per the spec — the batch is for the close, actionable band).

## 3. Interfaces

```
# src/mai/orchestrate/warm.py
async def _warm_plan(session, limit: int) -> list[str]
    # review PortVerdicts with band in (near, partial) AND no ReviewAdvice row yet;
    # near before partial; capped at `limit`. Returns item_ids ("{patch_group_id}:{core}").

async def warm_advice(session_factory, git_client, judge, *, limit=200, concurrency=4) -> dict
    # plan once (own session) -> fan out build_review_advice per item, each in its OWN session,
    # under Semaphore(concurrency). Returns {"planned": n, "warmed": n, "failed": n}.
```

Worker body (mirrors `enrich`'s worker): `async with sem: async with session_factory() as s:
out = await build_review_advice(s, git_client, judge, item_id)`; count `warmed` when
`out["opinion"] is not None`, else `failed`. A raised exception is caught → `failed` (the item
stays un-warmed, retried next run). `build_review_advice` already commits its own cache row.

## 4. CLI

`mai warm-advice [--limit N] [--concurrency K]`:
- Build the judge directly from `settings.openrouter_api_key` (running the command IS the opt-in;
  it does NOT require `review_advisor_enabled` — that flag only gates the web endpoint's
  auto-judge). If no key → exit with a clear message, warm nothing.
- Build `LocalGitClient` + use `SessionFactory`; call `warm_advice`; print
  `planned/warmed/failed`.

## 5. Invariants

1. **Bounded** — every run warms at most `--limit` items; `far`/unbanded excluded. No unbounded sweep.
2. **Resumable & idempotent** — each item commits its own cache row; a re-run skips already-cached
   items (plan excludes any with a `ReviewAdvice` row), so re-running converges to 0 new work.
3. **Best-effort** — a failed item (judge error, bad JSON) is counted and skipped; it does not abort
   the batch and is retried on the next run.
4. **Reuses L1; same model** — no new cache; warmed rows are the exact rows an on-demand expand
   reads (instant + free hit), because the batch routes the same model.
5. **No new authority** — warming only populates the advisory cache; it changes no verdict and
   merges nothing.

## 6. Out of scope (later B phases / future)

Cheap-model-first + escalation (splits the cache key; needs a UI "cheap → strong re-judge" path);
the multi-agent verify/skeptic lane (raises cost); auto-run in the refresh cycle; **the
porting-classification batch** (same primitive, work-list = decomp commits × target forks — the
direct step toward the end goal, but it needs the decomp-commit work-list resolution, which is its
own piece). Stale-row re-warming (Phase 1 skips any item with a row; the on-demand expand recomputes
stale ones) — a `--force` flag is a future refinement.

## 7. Testing

1. `_warm_plan`: seed review PortVerdicts across bands (near/partial/far/none) + some with a
   `ReviewAdvice` row → plan returns only near+partial WITHOUT a row, near before partial, capped at
   `limit`.
2. `warm_advice` (FakeJudge + FakeGitClient, temp-file sqlite so per-worker sessions share data):
   warms the planned items (cache rows written), returns `warmed == planned`; a second run → plan
   empty → `warmed == 0`.
3. Failure isolation: a FakeJudge that raises for one item → that item `failed`, the rest `warmed`,
   no exception escapes.
4. Concurrency: `limit`/`concurrency` honored; >1 item processed.
