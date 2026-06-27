---
title: "Mai — Chat-driven Porting Agent (program design)"
status: Draft
version: 0.1
date: 2026-06-27
owners: [r-log]
related:
  - review-advisor.md
  - review-advisor-p2-design.md
  - port-verdict-engine.md
  - memory-hierarchy-design.md
---

# Mai — Chat-driven Porting Agent (program design)

> **End goal (owner-stated):** sit in a chat, say *"port the m3 decomposition work to the
> sibling forks where it applies,"* and have Mai **classify, adapt, verify, and hand back
> reviewable ports** — grounded and human-gated at every step. This umbrella doc names the
> sub-projects, their order, and the invariants. Each sub-project gets its own spec → plan →
> build cycle.

This is a **program, not a single feature.** It is decomposed below; do not try to build it in
one pass.

## 1. Why this is reachable now

The hardest judgement — *"does this commit port to that fork, and how?"* — **already exists** as
the **portability classifier** (`mai.portability.classifier.evaluate(commit, target_core) ->
Verdict{state: PORTABLE | ADAPTABLE | NOT_APPLICABLE | ALREADY_PRESENT | UNCERTAIN, evidence}`),
backed by the **cppindex** tree-sitter C++ symbol gate. The classifier was built precisely to
answer *"which m3 commits port to which fork."* The end goal is that classifier **scaled across a
campaign, made actionable, and driven from chat.**

The **decomposition campaign is the ideal first target**: it is the *file-cohesion-split
pattern* — verbatim method-moves into `ClassXxx.cpp`, headers untouched, behavior preserved.
Mechanical, structural, low-risk. The safest possible thing to automate a port for.

**Already in the tree (the engine):** portability classifier · cppindex parser · review advisor
(grounded `ReviewOpinion` + `ground_opinion`) · the `/port/` board + `/api/review`. The
sub-projects below are the **chassis** around that engine.

## 2. End-to-end flow

```
chat prompt ("port Player.cpp decomp to zero")
  -> intent + work-list resolution   (which m3 commits, which target fork)        [D]
  -> classify each commit            (PORTABLE/ADAPTABLE/NOT_APPLICABLE)          [engine: classifier]
  -> for applicable commits:                                                       [C]
        PORTABLE   -> mechanical apply in an isolated per-fork worktree
        ADAPTABLE  -> LLM adaptation grounded in code-memory (rename/symbol fixups)
     verify        -> apply-check (+ scoped compile where available)
  -> propose reviewable diffs / draft PRs   (NEVER auto-merge)                     [D streams back]
```
Every LLM step is cached, cheap-model-first, and grounded; every stage is orchestrated by [B].

## 3. Sub-projects (dependency order)

| # | Sub-project | Purpose | Depends on |
|---|-------------|---------|-----------|
| **A** | **Memory hierarchy** | Cost + quality foundation: L1 verdict/advice cache · L2 code-memory (reuse cppindex) · L3 durable fork-divergence knowledge. Detailed in `memory-hierarchy-design.md`. | — |
| **B** | **Orchestration core** | "Superpowers inside Mai": a deterministic pipeline runner — plan → fan-out over a work-list → per-item judge/verify → synthesize. Cheap-model-first, gated escalation, caching at each stage. | A |
| **C** | **Porting executor** | Turn a verdict into a real change: PORTABLE → mechanical apply in an isolated worktree; ADAPTABLE → grounded LLM adaptation; verify (apply + scoped compile); emit a diff / draft PR. Starts **decomp-only**. | A, B, classifier |
| **D** | **Chat front door** | NL prompt → intent → work-list → run [B] → stream grounded results + diffs back. The "chat we'll build." | A, B, C |

## 4. Invariants (carried from the advisor — non-negotiable)

1. **Grounded & recomputable.** Memory is never authoritative; a remembered verdict/port is a
   cache keyed on its inputs, recomputed when they change. Ungrounded model claims never persist.
2. **Human-gated.** Mai *proposes* ports; it never merges or pushes. (Matches the workspace hard
   rule: never push to mangosthree; paired-fork discipline; Mai's own remote is r-log/Mai.)
3. **Cost-bounded.** Cache + cheap-model-first + gated multi-agent verify only on hard items.
   Memory IS the cost strategy — every layer removes or shrinks an LLM call.
4. **Decomp-first.** Begin with verbatim method-moves; expand to riskier change-classes only after
   that class is proven end-to-end.
5. **Deterministic spine.** Control flow (work-list, fan-out, verify gates) is deterministic
   Python; the LLM only *interprets/adapts* inside a stage, never steers the pipeline.

## 5. Phased roadmap

```
A (memory)            P3 cache [L1]  ->  code-memory [L2]  ->  durable knowledge [L3]
B (orchestration)                          pipeline runner  ->  cheap-first + verify lane
C (porting executor)                                            mechanical apply  ->  adapt
D (chat front door)                                                                  intent -> stream
```
Each box ships independently and is useful alone. **P3 cache is the first buildable phase** and
the base every later layer reuses (the orchestrator caches per stage; the executor caches its
verify results; the chat door reuses all of it).

## 6. Non-goals (this program)

- **No autonomy.** Mai never merges/pushes a port; a human reviews every proposed diff.
- **No expansion-logic porting in v1.** Cata-specific spell/raid/quest logic is *not* a target;
  only structural/mechanical changes (decomp first).
- **No new fork.** This operates over the existing mirrors (`mai/mirrors/{zero..four}.git`).
- **Not a CI/build farm.** "Scoped compile" in [C] is best-effort verification of a touched TU,
  not a full build pipeline (that stays the human's / CI's job).

## 7. Risks

- **Executor correctness** ([C]) is the highest-risk surface — mitigated by decomp-first
  (mechanical), apply+compile gates, and the human review gate.
- **Cost** without memory would be prohibitive at campaign scale — which is exactly why [A] is
  first.
- **Adaptation drift** (ADAPTABLE ports) — mitigated by grounding every adaptation in code-memory
  (real target symbols), not free generation.

## 8. Where the detail lives

- `memory-hierarchy-design.md` — Sub-project A (this is the next detailed spec; P3 cache is its
  Phase 1).
- Future: `orchestration-core-design.md` (B), `porting-executor-design.md` (C),
  `chat-front-door-design.md` (D) — written when each phase is reached.
