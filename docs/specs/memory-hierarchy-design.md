---
title: "Mai — Memory Hierarchy (Sub-project A) — design"
status: Approved
version: 1.0
date: 2026-06-27
owners: [r-log]
related:
  - porting-agent-program.md
  - review-advisor-p2-design.md
---

# Memory Hierarchy (Sub-project A) — design

The cost + quality foundation of the [[porting-agent-program]]. Three layers, each of which
**removes or shrinks an LLM call** — so "better memory" and "lower cost" are the same work. Built
phase by phase; **Phase 1 (L1 cache) is the immediate, fully-specified deliverable** and the base
the orchestrator and executor reuse.

## Layers at a glance

| Layer | What it remembers | Cost effect | Phase |
|-------|-------------------|-------------|-------|
| **L1 — Verdict/advice cache** | the grounded opinion per `(fix × fork × source_sha × target-HEAD × model × prompt_version)` | repeat expands cost **$0** | **1 (now — P3)** |
| **L2 — Code-memory** | per-fork structured symbol index (reuse `cppindex`) | smaller prompts (fewer tokens), cheaper evidence gathering, more accurate grounding | 2 |
| **L3 — Durable knowledge** | stable fork-divergence facts & confirmed decisions | less re-reasoning, consistent answers | 3 |

**Cross-layer invariant (from the program):** memory is **derived & recomputable**, never
authoritative. Each entry is keyed on its inputs and recomputed when they change. Ungrounded model
output never persists.

---

## Phase 1 — L1 verdict/advice cache (P3) — FULL SPEC

**Goal:** compute a review opinion by the LLM **at most once** per key; every later expand returns
the stored opinion instantly with **no billed call**.

### Data model — new table `review_advice`
SQLAlchemy model `ReviewAdvice` (cache-only — safe to drop/rebuild):
- **Identity (unique):** `(patch_group_id, core)`
- **Validity keys:** `source_sha`, `base_sha` (target HEAD), `model`, `prompt_version`
- **Payload:** `assessment`, `confidence` (grounded), `reason`, `tips` (JSON), `citations` (JSON),
  `adapted_hunks` (JSON), `grounded` (bool)
- `computed_at` (datetime)

### Read/write flow (inside `build_review_advice`)
1. Compute current keys: `source_sha` (from the verdict), `base_sha = git_client.head_sha(core)`,
   `model = choose_model(evidence, settings)`, `prompt_version = PROMPT_VERSION`.
2. Look up `ReviewAdvice` by `(patch_group_id, core)`. If a row exists AND its four validity keys
   **all match** → **hit**: return the stored opinion as the `opinion` dict; **no judge call**.
3. **Miss** → call judge + `ground_opinion` (as today); if a grounded opinion results, **upsert**
   the row and return it.
4. **Judge failure (opinion is None) → do NOT cache** — the next expand retries instead of caching
   a null.

### Invalidation
Purely key-based and automatic: target HEAD moves (`base_sha`), the router picks a different
`model`, or `PROMPT_VERSION` bumps → the validity keys no longer match → miss → recompute +
overwrite. No manual step.

### Migration & wiring
- New table is created by `Base.metadata.create_all` (works for *new* tables, unlike column-adds);
  plus a one-time create against the live `mai.db` (a tiny `mai-data/tmp/` script, like
  `migrate_portverdict.py`).
- `/api/review` response shape and the `renderAdvice` UI are **unchanged** — the `opinion` dict is
  identical, so no front-end work.
- `build_review_advice` already has `session`; it needs `git_client.head_sha(core)` (exists —
  `compute_verdicts` uses it; `FakeGitClient` scripts it).

### Decisions (confirmed)
- **Cache only successful opinions** (failures stay retryable).
- **No manual "re-roll"** — auto-invalidation suffices; a refresh button would just re-spend money.

### Tests
- miss → judge called once + a row is written;
- second identical call → **hit, judge NOT called** (`judge.calls == 1` across two expands);
- each validity-key change (`base_sha` / `model` / `prompt_version`) → miss → recompute;
- judge failure → not cached (retry);
- Invariant 1 still holds (non-review → no judge, no cache write).

---

## Phase 2 — L2 code-memory (design sketch)

Reuse the existing **`src/mai/cppindex`** tree-sitter C++ parser to build a **per-fork symbol
index** (functions, classes, members, file→symbol map) over the target worktrees, refreshed when a
fork's HEAD moves.

- **Cost effect:** evidence gathering for a review/port becomes a cheap index lookup instead of
  repeated `git show` / `read_region`; prompts carry **compact structured context** (the relevant
  symbols + signatures) rather than raw file dumps → fewer tokens per call.
- **Quality effect:** grounding gets a real symbol table to check against (sharper than substring
  matching); the classifier's Gate-3 symbol check reads the same index instead of re-parsing.
- **Shape (to be detailed in its own spec):** a `code_symbol` table keyed `(core, base_sha, path,
  symbol)` — itself an L1-style cache; an indexer that walks a worktree once per HEAD; a
  `lookup(core, path|symbol)` API the evidence builder and classifier call.
- **Invariant:** the index is derived from the worktree at a known `base_sha` and recomputed when
  HEAD moves — same recomputable discipline as L1.

## Phase 3 — L3 durable knowledge (design sketch)

A store of **stable facts the model should not re-derive**: fork-divergence policies (e.g. "Zero
stays pre-multilocale"), confirmed "already-handled" patterns, maintainer decisions — the analog
of a curated `MEMORY.md`.

- **Cost effect:** the judge reads only the *relevant* facts as grounded context instead of
  re-reasoning them every call; fewer escalations to the strong model.
- **Quality effect:** consistency — the same divergence fact yields the same verdict across items.
- **Shape (to be detailed in its own spec):** a `knowledge_fact` table (scope: global | core |
  subsystem; text; provenance; confidence); retrieval that injects the top-k relevant facts into
  the prompt; **write-back is gated** — a fact is recorded only from a human-confirmed decision or
  a high-confidence grounded verdict, never from raw model output.
- **Invariant (critical):** L3 must NOT become a place ungrounded claims accumulate. Every fact
  carries provenance and is revocable; facts are *context*, not *truth* — the deterministic
  evidence and the grounding guardrail still govern.

## Phasing rationale
L1 first because it is the immediate cost win and the literal base of the orchestrator/executor
(both cache per stage). L2 next because structured context is the biggest per-call token saving and
is reused by the classifier. L3 last because durable knowledge is the most judgement-heavy and
benefits from L1/L2 being in place.
