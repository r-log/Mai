---
title: "Mai — Review Advisor (grounded LLM tips on the REVIEW lane)"
status: Draft
version: 0.1
owners: [r-log]
related:
  - port-verdict-engine.md
  - conflict-closeness.md
---

# Mai — Review Advisor (grounded LLM tips on the REVIEW lane)

> The verdict engine grades ~5,272 (fix×core) pairs as **REVIEW** — "a human must judge." The
> Review Advisor makes that judgement fast and honest: it **collects deterministic evidence** per
> review item (the patch, which hunks reject, the target's code at those spots, similar commits in
> the target, the fix's intent), forwards it to an LLM for a **schema-validated opinion + concrete
> tips**, and then **drops any claim the evidence doesn't support** — so the AI can only ever opine
> on what it was actually shown. The opinion is **advisory** (a labeled tip + a grounded
> confidence), never an action. **NEEDS stays git-proven and LLM-free.** Borrows the proven
> collect→ground→advise pattern from GITA (reference only — no code copied). Audience: getMaNGOS
> maintainers + r-log.

<!-- Follows docs/specs/ numbered-section convention. Terse. -->

## 1. Summary

For a `verdict == "review"` item, `build_review_evidence` assembles — **offline, from the local
mirrors/worktrees** — a single evidence packet: the fix's intent (title/body/files/type), the
conflict in 3-way form (each patch hunk marked applied/rejected, the `.rej` text, and the target's
current code at that location), candidate **similar commits** already in the target core, and the
divergence reason. `ReviewJudge.judge(evidence)` forwards it to OpenRouter and returns a strict-JSON
`ReviewOpinion` (`assessment` ∈ portable/already_handled/divergent/uncertain, confidence, reason,
tips, optional adapted-hunk, citations). `ground_opinion` then **deletes every tip/citation that
does not reference something in the evidence** and **blends the model's confidence with its grounded
fraction**. The result is exposed at `GET /api/review/{item_id}` and rendered beside the review row
as labeled, advisory tips. Computed **on-demand** per item; cheap via prompt caching + context caps.

## 2. Goals & Non-Goals

**Goals**
- Cut human time per review: one panel with all the evidence **plus** an honest grounded opinion and
  concrete next-step tips ("trivial rename in hunk 9 — adapt to `Close()`", "likely already handled
  by #412").
- Stay **truthful**: every model claim is checked against the collected evidence; ungrounded claims
  are dropped before display, and confidence is mechanically discounted by groundedness.
- Be **cheap**: deterministic collection (git/DB lookups), prompt caching on stable prefixes, hard
  context caps, temperature 0, on-demand only, cheap-model-first routing.

**Non-Goals**
- **No change to NEEDS** — it stays git-proven and never touches the judge (Invariant 1).
- **No autonomy in v1** — the model decides nothing: it never claims, ports, resolves, or changes a
  verdict. Asymmetric auto-skip is explicitly deferred (future phase, out of scope here).
- **No tree-sitter `CodeIndex` in v1** — Mai reads the target worktree directly; symbol-memory is a
  later enhancement.
- **Not a replacement** for the closeness score (the batch triage) — this is the per-item deep dive.

## 3. Context & Constraints

- Builds on `port-verdict-engine.md` (`PortVerdict`, `LocalGitClient` per-core worktrees,
  `apply_fraction`) and `conflict-closeness.md` (`conflict_applied/total`, bands).
- Mai stays **offline** except the single LLM HTTP call: evidence comes from local bare mirrors +
  worktrees; only `ReviewJudge` hits OpenRouter.
- Reuse Mai's existing OpenRouter seam **pattern** (the `Enricher`): a narrow `ReviewJudge` protocol
  + `OpenRouterJudge` + `FakeJudge`; strict `response_format` JSON; `temperature=0`.
- Python 3.12 async; no new heavy deps (`httpx`, `pydantic` already present).
- GITA is **reference for the pattern only** — `views/diff_context`, `agents/pr_reviewer`
  (`verify_findings` guardrail, `structural_confidence`), `llm/client` (OpenRouter seam, prompt
  caching). No code is copied; field offsets/opcodes/idioms differ.

## 4. Invariants

1. **NEEDS is never LLM-touched.** The advisor runs **only** for `verdict == "review"`. A NEEDS
   verdict's truth is git-proven; nothing here can promote, demote, or annotate it.
2. **Grounded-only.** Every tip/claim the user sees must cite a file/line/commit **present in the
   collected evidence**; `ground_opinion` removes the rest. The model cannot opine on unseen code.
3. **Advisory, not deciding.** The opinion never changes a verdict, never claims/ports/resolves a
   task. It is a labeled tip + a confidence number.
4. **Deterministic-first.** All *facts* are collected deterministically (git/DB). The LLM only
   *interprets* what it was given; it contributes no new factual claim that survives grounding.
5. **Derived & recomputable.** The opinion is a cache keyed on `(source_sha, base_sha, model,
   prompt_version)`; it is never raw/authoritative and is recomputed when any key changes.

## 5. Data Model

- **`ReviewEvidence`** (assembled on demand; not necessarily persisted):
  `{item_id, fix:{title, body, subsystem, files[], magnitude, source_core, source_sha, type},
  conflict:{applied, total, band, hunks:[{index, applied:bool, patch_text, rej_text?,
  target_context?}]}, similar:[{sha, title, date, files[]}], divergence:{reason, classification}}`.
  `type` ∈ bugfix|refactor|docs|build|feature (path/keyword heuristic).
- **`ReviewOpinion`** (pydantic, the strict LLM schema):
  `{assessment: Literal["portable","already_handled","divergent","uncertain"], confidence: float,
  reason: str, tips: list[str], adapted_hunks: list[{path, suggestion}] = [], citations: list[str]}`.
- **`ReviewAdvice`** (optional cache table): key `(patch_group_id, core)`; cols `source_sha`,
  `base_sha`, `model`, `prompt_version`, `assessment`, `confidence` (grounded), `reason`,
  `tips` (JSON), `grounded` (bool), `computed_at`. Cache only — drop/rebuild freely.

## 6. Interfaces & Contracts

- **`GitClient` (extend)** — `rejected_hunks(core, patch_text, paths) -> list[{path, index,
  applied, patch_text, rej_text}]` (generalises `apply_fraction` to **return** the `.rej` content,
  not only count it); `read_region(core, path, start, end) -> str` (target code around a hunk, from
  the worktree); `similar_commits(core, paths, title, *, limit=3) -> list[{sha, title, date,
  files}]` (`git log -- <paths>` in the target mirror, ranked by title-token overlap). `FakeGitClient`
  gains scripted returns.
- **`build_review_evidence(session, git_client, item_id) -> ReviewEvidence`** (`sync/review.py`) —
  deterministic; resolves `item_id` → `PortVerdict` → fix files/diff → the packet above. Caps:
  `MAX_HUNKS`, `FILE_CONTEXT_CAP_CHARS`.
- **`ReviewJudge` protocol** — `async judge(evidence: ReviewEvidence) -> ReviewOpinion`.
  `OpenRouterJudge` (Mai's LLM seam; strict JSON; `temperature=0`; **prompt-cache** the stable
  system prompt + per-fix prefix; model routing: cheap default, `review_model_strong` for retry).
  `FakeJudge(responses)` for hermetic tests.
- **`ground_opinion(opinion, evidence) -> ReviewOpinion`** (the truthfulness lock) — drop any `tip`
  / `citation` / `adapted_hunk` not referencing a path/line/sha in `evidence`; set
  `confidence = llm_confidence * grounded_fraction`; if **nothing** grounds, force
  `assessment="uncertain", confidence=0.0` with a "model output ungrounded — manual review" note.
- **`build_review_advice(session, git_client, judge, item_id) -> {evidence, opinion}`** — orchestrate
  collect → judge → ground; read/write `ReviewAdvice` cache; per-item `try/except` (a judge failure
  yields `opinion=None`, never a crash).
- **API** — `GET /api/review/{item_id}` (session-gated) returns `{evidence, opinion}`. The board's
  review-row expand fetches it lazily.

## 7. Edge Cases & Failure Modes

| # | Case | Handling |
|---|------|----------|
| 1 | No OpenRouter key / offline | return **evidence only**, `opinion=null` — the 3-way view + similar commits still render; tips simply absent. |
| 2 | Binary / no-hunk patch | minimal conflict evidence; advisor notes "binary/blob — verify manually." |
| 3 | LLM returns malformed JSON | `LLMSchemaError` caught → `opinion=null` + flag; panel still shows evidence. One retry on `review_model_strong`, then give up. |
| 4 | Model hallucinates a citation | `ground_opinion` drops it; all-ungrounded → `uncertain`/0 with the manual-review note. |
| 5 | Huge fix (many files/hunks) | cap to `MAX_HUNKS` / `FILE_CONTEXT_CAP_CHARS`; note truncation in evidence. |
| 6 | `similar_commits` false positive | it is a **candidate**, advisory only; a human verifies; it never auto-marks `has_it`. |
| 7 | Stale cache (HEAD moved / prompt changed) | keyed on `source_sha`+`base_sha`+`model`+`prompt_version`; recompute on change. |
| 8 | Called for a non-review verdict | `build_review_advice` asserts `verdict=="review"`; otherwise returns evidence-only with no judge call (Invariant 1). |

## 8. Validation / Testing

1. **Collection (real-git):** crafted conflict — `rejected_hunks` returns the exact rejected hunk +
   `read_region` returns the target's code there; `similar_commits` finds a planted commit, ignores
   an unrelated one.
2. **Judge (fake):** `FakeJudge` canned `ReviewOpinion` → `build_review_advice` returns it; strict
   schema validated.
3. **Guardrail (headline):** an opinion citing a line **absent** from evidence ⇒ dropped; an
   all-ungrounded opinion ⇒ `assessment="uncertain"`, `confidence=0`. **Non-vacuous:** a grounded
   claim survives and keeps proportional confidence.
4. **Offline:** no key ⇒ evidence-only, `opinion=null`, endpoint still 200s with a usable panel.
5. **Determinism/cache:** same `(source_sha, base_sha, model, prompt_version)` ⇒ cache hit, no LLM
   call.
6. **Invariant 1:** assert the judge is **never** invoked for `needs`/`has_it`/`not_applicable`.

## 9. Phased Build

Each phase is independently shippable; the board keeps working throughout.

- **P1 — Evidence (deterministic, offline, no LLM).** `rejected_hunks` + `read_region` +
  `similar_commits` on `LocalGitClient`; `build_review_evidence`; `GET /api/review` returns
  evidence-only; the review-row expand renders the **3-way conflict view + similar commits +
  intent**. *Already hugely useful on its own — the reviewer stops leaving Mai.*
- **P2 — Judge + guardrail (the grounded opinion).** `ReviewJudge`/`OpenRouterJudge`/`FakeJudge` +
  `ReviewOpinion` schema + `ground_opinion` + `build_review_advice`; tips + grounded confidence shown
  beside the evidence; cost levers (prompt caching, caps, temp 0); config `review_advisor_enabled`,
  `review_model` / `review_model_strong`. **§8 gates 2–4, 6.**
- **P3 — Cache + cost polish.** `ReviewAdvice` table + `prompt_version`; cheap/strong routing; an
  optional bounded **batch pre-pass over the near/partial band only** (never the far tail).
- **Future (out of scope).** `CodeIndex` symbol-memory (tree-sitter) for richer context; asymmetric
  auto-skip of high-confidence non-ports — *only* if ever wanted, behind explicit config.

## 10. Glossary & References

- **code memory** — pre-collected structural context that makes data-gathering a cheap lookup
  (GITA's `CodeIndex`/`ImportEdge`; Mai v1 reads the worktree directly instead).
- **citation guardrail** — `ground_opinion`: deletes model claims not grounded in the evidence; the
  truthfulness lock (GITA's `verify_findings` analog).
- **grounded confidence** — `llm_confidence × grounded_fraction` (GITA's `structural_confidence`
  analog).
- **on-demand** — the advice is computed when a reviewer opens an item, not batched over 5,272.
- References: GITA `views/diff_context`, `agents/pr_reviewer/recipe` (two-stage + guardrails),
  `llm/client` (OpenRouter seam, prompt-cache rule). Builds on `port-verdict-engine.md` +
  `conflict-closeness.md`.
