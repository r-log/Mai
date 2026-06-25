# Step 0 — Portability classifier: discovery findings

Scratch note for the "evidence-based portability classifier" task. Written before any
implementation. Names the concrete integration point, the reusable git/checkout
mechanism, the symbol-index situation, and the reconciler seam — then records the
verified #229 fixture facts the acceptance test will assert.

## 1. Where port suggestions are generated (the integration point)

The "Ready to port" feed is **`PortVerdict.verdict == "needs"`**, surfaced through:

```
src/mai/sync/verdicts.py :: compute_verdicts(session, git_client)   <-- DECISION POINT
    writes PortVerdict rows (model: src/mai/db/models.py :: PortVerdict, line 329)
        |
src/mai/publish/dataviz.py :: build_port_verdicts(session)          <-- FEED (read-only group)
    one card per fix with >=1 needs|review core; verdict=="needs" => "needs porting" chip
        |
src/mai/web/board_api.py  -> GET /api/board  -> /port/ board UI
```

**The gating point is `compute_verdicts`.** Today its decision (verdicts.py:87–101) is:

```
paths_exist == none            -> not_applicable (file_absent)
apply_check(reverse) clean     -> has_it
apply_check() clean  + portable-> needs        <-- #229 FALSE POSITIVE lands HERE
apply_check() clean  + diverg. -> review
apply_check() conflict         -> review
apply_check() file_absent      -> not_applicable
```

`relevance` (portable|divergent) comes from `resolve_relevance` over `SubsystemClass`
(`shared|client_bound|expansion|vendored|mixed`). `src/shared/...` classifies **shared**,
so #229 into Zero/One/Two reaches `clean + portable -> needs` and is wrongly suggested.

The record type is **`PortVerdict`** (uq `patch_group_id + core`); the human-intent
overlay `BoardItem` keys on `f"{patch_group_id}:{core}"` and is **untouched** by this work.

`build_port_candidates` / `PortCandidate` (models.py:265) is the OLD path, board-unused;
leave it alone.

## 2. Local checkouts of each core (reused, not reinvented)

`src/mai/git/client.py :: LocalGitClient` already provides everything the gates need,
over **bare `--mirror` clones at `mai/mirrors/{zero,one,two,three,four}.git`** (all 5
present, verified). Config: `git_mirror_dir`, `git_worktree_dir`.

| Need | Existing method | Notes |
|------|-----------------|-------|
| Target working tree at HEAD | `ensure_worktree(core)` | hot-path cached; worktree at `mai/worktrees/{core}`, `core.autocrlf=false` forced |
| Gate 1 equivalence | `_patch_id` (`git patch-id --stable`) + `apply_check(reverse=True)` | patch-id already drives `PatchGroup`/`Propagation`; reverse-clean already => `has_it` |
| Gate 2 mechanical apply | `apply_check(core, patch)` -> `clean\|reverse_clean\|conflict\|file_absent` | never raises; result is the router |
| Source patch text | `diff(core, sha)` (`diff-tree --root -p -M`) | same diff that feeds patch-id |
| File presence | `paths_exist(core, paths)` | one `cat-file --batch-check` |
| Conflict closeness | `apply_fraction` | already feeds REVIEW banding |

**No new clone path needed.** Gate 3 reads the touched file out of the same target
worktree (`ensure_worktree`) — `git -C <wt> show HEAD:<path>` or read from disk.

## 3. Tree-sitter / AST / symbol index — DOES NOT EXIST

- No `tree_sitter` / AST / symbol-index code anywhere under `src/` (grepped).
- `tree-sitter` is **not** a dependency (`pyproject.toml`); deps are sqlalchemy / pydantic
  / httpx / fastapi / argon2 — no parsing libs.
- GITA (`r-log/GITA`) is a **separate repo, not vendored locally** and not importable.

**GITA evaluated (cloned to `MANGOS/GITA`).** It is a clean Tree-sitter→Postgres indexer,
but its grammar coverage is **Python / TypeScript / JavaScript only** (`LANGUAGE_BY_EXT`,
`queries/{python,typescript,javascript}.scm`, `ts_loader._build_language`) — **no C++**.
Taking a package dependency on GITA would also drag its GitHub-App stack (`asyncpg`,
`alembic`, `arq`, `pgvector`, `fastapi`, `openai`) into mai. So GITA is **not** imported.

**Decision (owner-directed): mai gets its own purpose-built C++ parser.** Adopt GITA's
*pattern* (Tree-sitter `Language → Parser → query → extract`), not its package. New deps,
flagged + approved: **`tree-sitter` + `tree-sitter-cpp`** (two pip wheels, compiled C, no
DB — not "heavy" like Postgres). The extraction layer is mai-owned and scoped to exactly
what the portability gates ask: function definitions, their parameter lists, locals, and
file-level symbol definitions — not a generic whole-repo index.

**Foundation proven** (mai env = pyenv **3.12.8**, the `python` shim set by `.python-version`):
`tree-sitter-cpp` parses zero's real `DBCFileLoader.cpp` and returns
`AutoProduceStrings(const char* format, char* dataTable)` — no `loc`. AST is required (not
regex): a bare `loc` substring would false-match `block`/`allocate`; the `parameter_list`
node is exact.

**Gate 3 rule (grounded in the real #229 hunk):** a *free* identifier in the patch's added
code (referenced, but not declared within the added lines, not a C++ keyword) that resolves
in the **source** function's scope but is **absent** from the **target**'s scope is a missing
precondition. For #229: added code uses `loc`; source `AutoProduceStrings` has it as a param,
target does not, and it is no local/file symbol there → `loc` unresolved → NOT_APPLICABLE.
`holder`/`st` are introduced by the patch (skip); `getRecord`/`stringPool`/`stringTable`/
`offset`/`x`/`y` resolve in both forks (present, not flagged). Confirmed: one + two also lack
the `loc` param, so all three targets → NOT_APPLICABLE.

Python: stay on pinned **3.12.8**. A 3.13 bump is a separate, riskier change (300 tests,
asyncpg/pgvector wheels) with no parser-quality benefit — noted as optional follow-up.

## 4. Reconciler / job system (recompute on HEAD move)

There is **no ARQ** (not a dependency). The reconciler is a plain function:

```
src/mai/refresh/cycle.py :: run_refresh_cycle(...)
   commits-harvest -> PR-harvest -> compute_propagation -> classify_subsystems
   -> compute_port_candidates -> compute_verdicts(session, git_client)  <-- our hook
   -> reconcile_board -> publish_site
src/mai/refresh/trigger.py :: run_cron(Clock, ...)   # cron backstop loop
```

`compute_verdicts` is **already incrementally cached** on `(source_sha, base_sha)` where
`base_sha = git_client.head_sha(target_core)` — i.e. it already recomputes when a target
HEAD moves. **Add `gate_suite_version`** to that cache key so a change to the gate logic
invalidates stale verdicts. No new job system required; the classifier rides the existing
`compute_verdicts` call inside `run_refresh_cycle`.

## 5. Verified #229 fixture facts (real mirrors, not hand-waved)

Commit: **three `78b7a9951`** — "[DBC] Populate localized DBCString fields in
AutoProduceStrings (#229)", touches `src/shared/DataStores/DBCFileLoader.cpp`.

Added lines reference (preconditions the patch depends on but does NOT introduce):
`holder`, `loc`, `MAX_LOCALE`, `AutoProduceStringsArrayHolders`.

| | `AutoProduceStrings` signature @ HEAD | holder layout |
|--|--|--|
| **three (source)** | `(const char* format, char* dataTable, LocaleConstant loc)` | `AutoProduceStringsArrayHolders` present |
| **zero (target)** | `(const char* format, char* dataTable)` — **no `loc`** | **absent** (`MAX_LOCALE`/holder grep empty) |

(One/Two expected identical to Zero — to be confirmed in the test.) The file **exists** in
Zero, so `paths_exist` passes and the textually-similar single-pointer removed-context can
let `git apply` succeed → only the **symbol-precondition gate** yields the truthful
`NOT_APPLICABLE`. Evidence must name the missing `loc` parameter + holder symbols.

**Positive control:** the last 5-fork run produced 36 genuine `needs`, all in
`src/shared/*` / `src/tools/Extractor*` / `src/realmd`. Pick one that applies clean with no
missing symbols and assert `PORTABLE`; if none is clean enough for a stable golden, build a
minimal fixture repo rather than fake the verdict.

## Proposed module layout (for the plan step)

```
src/mai/portability/
    __init__.py
    types.py        # Verdict, Evidence, State enum, GATE_SUITE_VERSION
    symbols.py      # extract required-symbols from a patch; scoped presence check (Gate 3)
    classifier.py   # evaluate(commit, target_core, *, git_client, classes) -> Verdict
                    #   Gate 1 (patch-id/reverse) -> Gate 2 (apply) -> Gate 3 (symbols)
```

Integration: `compute_verdicts` calls `evaluate(...)`, stores the result in a NEW additive
`PortVerdict.state` column (+ keep `verdict`/`evidence` as-is). `build_port_verdicts` is
**NOT** repointed to `state` in this phase — that feed-flip is the final, separately
reviewable switch, called out explicitly and left OFF.

## BUILT (Phase 1) — status

Shipped, tested (328 passed incl. real-mirror golden):
- `src/mai/cppindex/` — purpose-built C++ parser (tree-sitter-cpp engine; functions,
  params, locals, file symbols, line-scoped reference/introduced extraction).
- `src/mai/portability/` — `types.py` (State/Evidence/Verdict/GATE_SUITE_VERSION),
  `patch.py` (unified-diff reader), `symbols.py` (Gate 3), `classifier.py`
  (`evaluate` + shared `classify_from_apply`, Gates 1→2→3).
- `git/client.py`+`fake.py` — `read_file(core, ref, path)`.
- `db/models.py` — additive `PortVerdict.state` / `state_evidence` / `gate_version`.
- `sync/verdicts.py` — `compute_verdicts` now ALSO stores `state` (reuses its
  apply_result; `gate_version` added to the incremental cache key). `verdict` unchanged.
- Acceptance: `evaluate(#229, {zero,one,two}) == NOT_APPLICABLE` (names `loc`), positive
  control `== PORTABLE`. Real verdict objects pasted in the session report.

## THE SWITCH — deliberately NOT flipped (final reviewable change)

The live "Ready to port" feed still reads `verdict`. To make it consume the classifier,
change ONE place — `build_port_verdicts` in `src/mai/publish/dataviz.py` (~line 268):

```
# now:        if v.verdict == "needs":  needs.append(...)
# flip to:    state == "portable" -> the only "ready to port"; route
#             "adaptable" / "not_applicable" / "already_present" to sibling buckets
#             (audit false negatives too); show none silently.
```

`reconcile_board` (refresh/cycle.py) currently archives off `needs|review` — flip it to the
new actionable set (`portable`+`adaptable`) in the same change. Until then the classifier is
pure shadow data: computed, stored, evidenced, consumed by nothing. Flip only after review.

## Out-of-scope seams (TODO markers in `classifier.py`)

necessity join · policy/divergence facts · compile probe · LLM adjudication of UNCERTAIN ·
rename/move correspondence (uses new_path today) · per-hunk split for non-atomic commits.

## Optional follow-up
Python 3.13 bump (separate, risk-bearing — no parser-quality gain). Gate-3 perf: on the
5-fork run, clean/conflict C++ pairs now do 2 `git show` + 2 parses each; fine for
correctness-first Phase 1, tune if `sync-analyze` wall-clock regresses.
