---
title: "Mai — L2 Code-Memory (Sub-project A, Phase 2) — design"
status: Approved
version: 1.0
date: 2026-06-27
owners: [r-log]
related:
  - memory-hierarchy-design.md
  - porting-agent-program.md
---

# L2 Code-Memory (Phase 2) — design

Phase 2 of the [[memory-hierarchy-design]]. A persistent **code-symbol index over the target
worktrees**, so the repeated tree-sitter parses that `cppindex` does today become a cached lookup.
**Scope decision (owner-approved): substrate first** — build the index + cache + lookup API and wire
the **portability classifier** through it. Behavior-preserving (identical verdicts, far fewer
parses → faster `sync-analyze`). The advisor-prompt token win is **L2b**, a later phase.

## 1. What exists today

`cppindex` (`src/mai/cppindex/extract.py`) is **stateless**: every `functions()` / `find_function()`
/ `file_symbols()` re-reads a file's bytes and re-parses. The **only** consumer is the classifier's
Gate 3 (`portability/symbols.py`), which on **every** `evaluate()` reads the target file
(`read_file(target_core, target_head, path)`) and calls `extract.file_symbols(target_bytes)` +
`extract.find_function(target_bytes, name)`. That `(target_core, target_head, path)` tuple is the
cache key, and the classifier runs over ~9,692 verdicts per `sync-analyze`.

## 2. Firmed decisions

- **Per-file index row** (NOT per-symbol). Access is always whole-file; one row per
  `(core, base_sha, path)` with the file's symbols + functions as JSON — the same L1-cache shape as
  `ReviewAdvice`. (Per-symbol cross-file querying — for the executor's rename maps — is a future
  extension, noted, not built.)
- **`FileIndex` object** is a faithful drop-in for the `extract.*` target-side calls:
  `file_symbols: set[str]`, `functions: list[CppFunction]` (in tree order), `find_function(name)` =
  first fn with `.name == name` (mirrors `extract.find_function`), `exists: bool`.
- **`get_file_index` is best-effort + recomputable** (the ReviewAdvice discipline): cache hit →
  reconstruct from JSON; miss → `read_file` + `extract.functions`/`extract.file_symbols` + upsert;
  a missing session or any cache I/O fault → parse directly, skip persistence (never breaks the
  classifier). Recomputed when `base_sha` (the fork HEAD) moves.
- **Source side stays on bytes.** Gate 3's source-side extraction (`identifiers_on_lines`,
  `declared_on_lines`, `function_covering`/`find_function`, `file_symbols` on source) is per-patch
  and line-dependent — not cacheable by `(HEAD, path)`. Only the **target** side is indexed.

## 3. Data model — new table `code_file_index`

`CodeFileIndex` (cache-only, drop/rebuild-safe):
- **Identity (unique):** `(core, base_sha, path)`
- `exists: bool` (False if the file is absent at that ref)
- `file_symbols: JSON` (list — the `set[str]` serialized)
- `functions: JSON` (list of `{name, qualified_name, start_line, end_line, params, locals}`,
  in `extract.functions` order)
- `indexed_at: datetime`

## 4. Interfaces

```
# src/mai/codememory/index.py
@dataclass
class FileIndex:
    exists: bool
    file_symbols: set[str]
    functions: list[CppFunction]          # tree order
    def find_function(self, name: str) -> CppFunction | None   # first .name == name

async def get_file_index(session, git_client, core, base_sha, path) -> FileIndex
    # cache hit -> reconstruct; miss -> read_file + extract + upsert; session None / cache
    # fault -> direct parse, no persist. exists=False when read_file returns None.
```

`FileIndex.find_function` and `.file_symbols` return EXACTLY what `extract.find_function(bytes,
name)` and `extract.file_symbols(bytes)` would for the same file content — verified by a test that
parses the same bytes both ways.

## 5. Classifier wiring (behavior-preserving)

- **`missing_in_file`** (`portability/symbols.py`): replace the `target_bytes: bytes | None` param
  with `target_index: FileIndex | None`. `None` or `not target_index.exists` → return `[]` (same as
  the old `target_bytes is None`). Inside: `tgt_fn = target_index.find_function(name)`,
  `tgt_syms = target_index.file_symbols`. Source side unchanged.
- **`classify_from_apply`** (`portability/classifier.py`): gains an optional `session=None`; per
  touched cpp file, resolve `target_index = await get_file_index(session, git_client, target_core,
  target_head, path)` and pass it to `missing_in_file` instead of reading+encoding target_bytes.
  `evaluate` and `compute_verdicts` pass their session (or None).
- **Guarantee:** identical `Verdict` output. The `#229` golden (`evaluate(#229,{zero,one,two}) ==
  NOT_APPLICABLE` naming `loc`) and all `test_portability_*` tests must pass unchanged.

## 6. Invariants

1. **Derived & recomputable** — keyed on `(core, base_sha, path)`; a fork HEAD move re-indexes.
   Never authoritative.
2. **Behavior-preserving** — the classifier's verdicts are byte-identical with the index on or off;
   the index is a pure parse cache.
3. **Best-effort** — a missing table / read / parse fault degrades to a direct parse (or, for an
   absent file, `exists=False`); it never makes the classifier raise.

## 7. Cost / performance effect

`sync-analyze` parses each target file **once per HEAD** instead of once per verdict that touches
it — the dominant repeated cost in Gate 3. No LLM tokens involved (the classifier is deterministic);
the LLM-token win is L2b (feeding `FileIndex` symbols into advisor prompts).

## 8. Testing

1. `CodeFileIndex` row round-trips (functions + file_symbols JSON).
2. `get_file_index`: miss reads+parses+writes one row; second call (same key) reconstructs WITHOUT
   re-parsing (assert via a parse-counter / spy on `extract.functions`); HEAD (base_sha) change →
   re-index.
3. **Fidelity:** `FileIndex.find_function(name)` / `.file_symbols` == `extract.find_function(bytes,
   name)` / `extract.file_symbols(bytes)` on the same real C++ fixture.
4. Best-effort: `session=None` → direct parse, correct result, no row; absent file → `exists=False`.
5. `missing_in_file` with a `FileIndex` reproduces the old `target_bytes` behavior (reuse the #229
   fixture: target lacking `loc` → flagged).
6. **Golden:** the classifier `#229` acceptance + `test_portability_*` pass unchanged.

## 9. Out of scope (future)
Per-symbol cross-file index (executor rename maps); source-side caching; **L2b** — feeding
`FileIndex` symbol context into the advisor/executor prompts to shrink tokens (its own spec, with
opinion-quality validation).
