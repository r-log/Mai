---
title: "Mai — Review Advisor P2 (grounded LLM opinion) — design"
status: Approved
version: 1.0
date: 2026-06-27
owners: [r-log]
related:
  - review-advisor.md          # master spec (this resolves its P2 deltas)
  - 28-review-advisor-p1-evidence.md
---

# Review Advisor P2 — Judge + Guardrail (design)

Resolves the open decisions for `review-advisor.md` §9 **P2** and is the source of truth
the implementation plan executes against. P1 (evidence collection) shipped at origin/main
`253fda7`. This phase adds the **grounded LLM opinion** on top of the existing evidence packet.

## 1. Scope

**In P2:**
- `src/mai/judge/` — `ReviewJudge` protocol + `OpenRouterJudge` + `FakeJudge` + `choose_model`
  router + `ReviewOpinion` schema + `ground_opinion` guardrail + prompt.
- `build_review_advice(session, git_client, judge, item_id)` in `sync/review.py`
  (collect → judge → ground; per-item try/except).
- `GET /api/review/{item_id}` now returns `{evidence, opinion}` (was `{evidence}`).
- Board review-row panel renders the opinion (assessment badge, grounded confidence, tips,
  adapted-hunk suggestions) beside the existing evidence.
- Config flag `review_advisor_enabled` (default **off**); model + threshold settings.

**Deferred to P3 (unchanged from master spec §9):** the persistent `ReviewAdvice` cache
table, `prompt_version`, and any batch pre-pass. **P2 computes on-demand, no DB persistence**
— recomputed each time a reviewer opens an item. Bounded because it is human-triggered, one
item at a time.

**Invariant 1 (absolute):** the judge is **never** invoked for a non-`review` verdict. NEEDS
stays git-proven and LLM-free.

## 2. Component layout (mirrors the proven `src/mai/enrich/` seam)

```
src/mai/judge/
    __init__.py
    schema.py    # ReviewOpinion (pydantic) + parse_opinion + ReviewOpinionSchemaError
    prompt.py    # SYSTEM_PROMPT + build_prompt(evidence) -> str
    judge.py     # ReviewJudge Protocol + OpenRouterJudge + choose_model(evidence, settings)
    fake.py      # FakeJudge(responses) for hermetic tests
    ground.py    # ground_opinion(opinion, evidence) -> ReviewOpinion   (the truthfulness lock)
```

`OpenRouterJudge` mirrors `OpenRouterEnricher` exactly: httpx POST to
`{base}/v1/chat/completions`, `response_format={"type":"json_object"}`, `temperature=0`,
`messages=[{system: SYSTEM_PROMPT},{user: build_prompt(evidence)}]`; a 200 with non-JSON /
empty content raises `ReviewOpinionSchemaError`.

## 3. Data model

**`ReviewOpinion`** (pydantic, strict LLM schema):
```
assessment: Literal["portable","already_handled","divergent","uncertain"]
confidence: float            # 0..1 (model's self-reported; replaced by grounded value)
reason: str
tips: list[str] = []
adapted_hunks: list[{path: str, suggestion: str}] = []
citations: list[str] = []    # each must reference a path / "path:line" / sha in evidence
```
`build_review_advice` returns `{"evidence": <P1 packet>, "opinion": <ReviewOpinion dict|null>}`.

## 4. Model router — "work together" (content-aware, deterministic)

`choose_model(evidence, settings) -> str`:
- if `evidence.conflict.total > settings.review_hunk_routing_threshold` **OR** the estimated
  prompt size (sum of hunk `patch_text` + `target_context` chars) `> settings.review_large_context_chars`
  → `settings.review_model_large` (**Gemini Pro** — large window for many-hunk fixes).
- else → `settings.review_model` (**Claude Sonnet** — focused diff/conflict judgment).

One call per item, **no retry-escalation** (confirmed). temperature 0 both ways.

## 5. Config additions (`src/mai/config.py`)

```python
review_advisor_enabled: bool = False
review_model: str = "anthropic/claude-sonnet-4.6"      # verify exact OpenRouter slug at impl
review_model_large: str = "google/gemini-2.5-pro"      # verify exact OpenRouter slug at impl
review_hunk_routing_threshold: int = 8                  # > this many hunks -> large model
review_large_context_chars: int = 24000                # or this many prompt chars -> large model
```
Reuses existing `openrouter_api_key` / `openrouter_api_url`. The judge is constructed only when
`review_advisor_enabled and openrouter_api_key`; otherwise `opinion=null`.

## 6. Data flow

```
review-row expand -> GET /api/review/{id}
  -> build_review_evidence(...)                         # P1, deterministic, always
  -> if review_advisor_enabled and key and verdict=="review":
         model = choose_model(evidence, settings)
         opinion = ground_opinion(await judge.judge(evidence, model), evidence)
     else:
         opinion = null
  -> {evidence, opinion}
```

## 7. Grounding guardrail (Invariant 2 — the truthfulness lock)

`ground_opinion(opinion, evidence)`:
- Build the evidence reference set: every file `path`, every `path:line` derivable from a hunk's
  `target_line`, every similar-commit `sha`, the `source_sha`.
- Drop any `tip` / `citation` / `adapted_hunk` that does not reference a member of that set.
  (A tip is kept if it cites a grounded token; an adapted_hunk is kept only if its `path` is in
  evidence.)
- `grounded_fraction` = surviving claims / original claims (1.0 if the model made no claims to
  check beyond `assessment`+`reason`).
- `confidence = llm_confidence * grounded_fraction`.
- If **nothing** grounds (all tips/citations/adapted_hunks dropped and there were some) →
  force `assessment="uncertain", confidence=0.0`, append a "model output ungrounded — manual
  review" note to `reason`.

## 8. Error handling & edge cases (master spec §7)

| Case | Handling |
|------|----------|
| No key / advisor disabled / offline | `opinion=null`; evidence renders; endpoint 200. |
| Malformed/empty JSON from model | `ReviewOpinionSchemaError` caught → `opinion=null`. **Zero retry** (confirmed). |
| Non-review verdict | judge never called (Invariant 1); evidence-only. |
| Huge fix | evidence already capped in P1 (MAX_HUNKS / context cap); router sends it to the large model. |
| Hallucinated citation | dropped by `ground_opinion`; all-ungrounded → uncertain/0. |
| Any judge exception | per-item try/except in `build_review_advice` → `opinion=null`, never a 500. |

## 9. UI (portboard.js + board.css)

Below the existing evidence block, when `opinion` is non-null, render:
- an **assessment badge** (portable / already-handled / divergent / uncertain, colour-coded),
- **grounded confidence** as a percentage (label it "grounded confidence" so it is not read as
  certainty),
- **tips** as a list,
- **adapted-hunk suggestions** (path + suggestion) where present.
All server strings go through `esc()`. When `opinion` is null, the panel is exactly the P1
evidence view (no empty opinion box).

## 10. Testing (master spec §8 gates 2–4, 6)

1. `FakeJudge` canned `ReviewOpinion` → `build_review_advice` returns `{evidence, opinion}`;
   strict schema parse validated; malformed → `ReviewOpinionSchemaError`.
2. **Grounding (headline):** ungrounded citation dropped; all-ungrounded → `uncertain`/0 +
   note; a grounded claim **survives** with proportional confidence (non-vacuous).
3. **Offline:** no key → `opinion=null`, endpoint 200, evidence intact.
4. **Invariant 1:** judge is never invoked for `needs`/`has_it`/`not_applicable`.
5. **Router:** `choose_model` → small evidence picks `review_model`, many-hunk / large-context
   picks `review_model_large`.
6. **OpenRouterJudge:** mocked-httpx round-trip (mirror `test_openrouter_enricher`): strict JSON
   in → `ReviewOpinion`; non-JSON 200 → `ReviewOpinionSchemaError`.

## 11. Out of scope (P3+ / future)
Persistent `ReviewAdvice` table + `prompt_version`; cheap/strong cost routing beyond the
size-based selector; batch pre-pass over the near/partial band; tree-sitter `CodeIndex`
symbol-memory; any autonomy (the opinion never changes a verdict).
