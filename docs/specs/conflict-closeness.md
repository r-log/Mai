---
title: "Mai — Conflict Closeness (applied-hunk fraction)"
status: Draft
version: 0.1
owners: [r-log]
related:
  - port-verdict-engine.md
---

# Mai — Conflict Closeness (applied-hunk fraction)

> The Port-Verdict engine grades a fix that doesn't apply cleanly as **REVIEW (conflict)** — but
> on real three↔two data that's **637 conflicts in one undifferentiated lane**: a 1-line-context
> drift sits next to a totally-diverged file. This spec adds a **closeness score** to conflict
> verdicts: how much of the fix actually applies, by hunk. `git apply --reject` lands the hunks
> it can and writes a `.rej` for each it can't, so **applied / total hunks** gives a sortable
> 0–100%. REVIEW becomes a ranked backlog — the near-portable fixes (the realistic next ports)
> rise to the top. It refines the REVIEW lane only; the truthfulness gate is untouched (NEEDS
> still requires clean apply **and** all-shared).

<!-- Follows the docs/specs/ numbered-section convention. Terse. -->

## 1. Summary

For a verdict that grades `review` because the forward `git apply --check` returned `conflict`,
the engine measures **how close** the fix is to applying by re-running it with `git apply --reject`
against the target worktree and counting hunks: `total` from the patch's `@@` headers, `rejected`
from the `@@` headers in the `<path>.rej` files git wrote for the touched paths, `applied = total −
rejected`. The verdict stores `conflict_applied` / `conflict_total`; the evidence gets a
**near / partial / far** label by threshold. The board (Phase 4) sorts REVIEW-conflicts by
`applied/total`. Audience: getMaNGOS maintainers + r-log.

## 2. Goals & Non-Goals

**Goals**
- Give every `conflict` verdict a **sortable closeness** (`applied`, `total`, fraction) so REVIEW
  ranks near-portable fixes above hard ones.
- Reuse the existing per-core worktree + git machinery; no new clones, no full-tree scans (look only
  at the patch's touched `.rej` paths).
- Honest gradation: `near` ≥ 80% / `partial` 40–80% / `far` < 40% (tunable), surfaced as evidence.

**Non-Goals**
- **No change to the truthfulness gate.** NEEDS still = clean apply **and** all-shared; closeness only
  refines `review`-conflict ordering.
- **No board rendering here.** Computing + storing the score is this spec; the board sorting/showing it
  is Phase 4.
- **No semantic merge / 3-way.** A cross-mirror 3-way needs the source's base blobs in the target object
  store (separate mirrors don't share recent objects) — out of scope. Hunk applicability is the signal.
- **No closeness for non-conflicts.** `clean` / `has_it` / `not_applicable` verdicts carry no fraction.

## 3. Context & Constraints

- Builds on `port-verdict-engine.md`: `LocalGitClient` (per-core worktrees, `apply_check`, `diff`,
  `paths_exist`), `compute_verdicts` (the verdict stage), `PortVerdict` model, `FakeGitClient`.
- `git apply --reject` **modifies the worktree** (applies clean hunks, writes `.rej`). The next fix's
  `apply_check` calls `ensure_worktree`, which `reset --hard` + `clean -fdq` — so the dirty state is
  wiped before the next measurement. No extra reset needed (relies on the existing per-call refresh).
- A patch with **no hunks** (binary, pure rename) → `total = 0` → no fraction (`conflict_applied` /
  `conflict_total` stay null); the verdict is still `review`.
- Constraints: Python 3.12, async subprocess; `Fake*` seams; 4-space indent; `feat:` commits; no AI
  attribution; no new deps. Real-git tests gated `skipif(git not on PATH)`.

## 4. Invariants

1. **Closeness never changes a verdict.** It is computed *after* the verdict is `review`/`conflict`;
   it only annotates. NEEDS/REVIEW/N-A/HAS-IT are decided exactly as before.
2. **Only conflict verdicts carry a fraction.** All other verdicts leave `conflict_applied`/`_total` null.
3. **Targeted, bounded I/O.** `.rej` counting reads only `<wt>/<path>.rej` for the patch's touched paths —
   never a recursive worktree scan.
4. **Derived & recomputable.** The fields are part of the recomputed `PortVerdict`; incremental cache key
   (`source_sha`, `base_sha`) is unchanged.

## 5. Data Model

`PortVerdict` (extend): add `conflict_applied: int | None` and `conflict_total: int | None` (nullable;
set only when `verdict == "review"` and `apply_result == "conflict"` and the patch has ≥1 hunk).
`closeness = conflict_applied / conflict_total` is derived (not stored). No other model change.

## 6. Interfaces & Contracts

- **`GitClient.apply_fraction(core, patch_text, paths) -> tuple[int, int]`** (new) — returns
  `(applied, total)`. `LocalGitClient`: `ensure_worktree`, `git apply --reject -` (via `_run_raw`,
  never raises), `total` = count of `^@@ ` in `patch_text`, `rejected` = sum of `^@@ ` across the
  existing `<wt>/<p>.rej` files for `p in paths`, `applied = max(0, total − rejected)`. If `total == 0`,
  return `(0, 0)`. `FakeGitClient`: keyword `fractions={(core, patch): (applied, total)}`, default `(0, 1)`.
- **`compute_verdicts`** — in the conflict branch (the existing `else: verdict = "review"` after a
  non-clean, non-file-absent forward apply), call `apply_fraction(target_core, patch, paths)`; if
  `total > 0`, set `conflict_applied`/`conflict_total` and append evidence
  `"conflict: {applied}/{total} hunks apply ({label})"` where `label` = near (≥0.8) / partial (≥0.4) /
  far (<0.4). Pass the fields through `PortVerdictRepository.upsert`.

## 7. Edge Cases

| # | Case | Handling |
|---|------|----------|
| 1 | binary / no-hunk patch conflicts | `total = 0` → fields null; verdict `review`. |
| 2 | every hunk rejects | `applied = 0`, fraction 0 → `far`. |
| 3 | `.rej` written for a file not in `paths` (rename edge) | counted only for `paths`; a missed `.rej` undercounts rejects → fraction slightly high, never a false NEEDS (verdict already review). Acceptable, bounded. |
| 4 | worktree left dirty after `--reject` | next `ensure_worktree` reset+clean wipes it (existing behavior). |
| 5 | non-conflict verdict | `apply_fraction` not called; fields null. |

## 8. Validation / Testing

- **Real-git:** a multi-hunk patch where some hunks apply and ≥1 conflicts → assert `(applied, total)`
  is the true split and the verdict is `review` with the closeness evidence.
- **Fake:** `compute_verdicts` records `conflict_applied`/`_total` for a scripted conflict; asserts
  clean/has_it/not_applicable verdicts leave them **null**.
- **Thresholds:** unit-test the near/partial/far labelling at boundaries (0.8, 0.4).
- No regression: the existing verdict tests (truthfulness gate, cache, multi-fix isolation) still pass.

## 9. Phased Build

Single focused change (one plan): (1) `apply_fraction` on the git client + protocol + Fake; (2) the
`PortVerdict` fields + `compute_verdicts` conflict-branch integration + the near/partial/far label;
(3) real-git + fake tests. The board's sort/render by closeness is **Phase 4** of the verdict engine,
not here.

## 10. Glossary & References

- **closeness** — `applied / total` hunks of a conflicting fix; 1.0 would be clean (but clean never
  reaches this path).
- **applied-hunk fraction** — measured via `git apply --reject` (.rej hunk counting).
- **near / partial / far** — closeness bands (≥0.8 / ≥0.4 / <0.4), tunable.
- Builds on `port-verdict-engine.md` (the verdict stage); consumed by its Phase 4 board re-model.
