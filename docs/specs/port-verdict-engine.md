---
title: "Mai — Port-Verdict Engine (applicability-graded, relevance-gated)"
status: Draft
version: 0.1
owners: [r-log]
related:
  - sync-intelligence-engine.md
  - port-debt-board-multiuser.md
  - framework-architecture.md
---

# Mai — Port-Verdict Engine (applicability-graded, relevance-gated)

> Today's engine flags port-debt from `patch-id` absence: a fix present in one fork
> and absent (by exact diff) in another, whose files sit in a `shared/` path. That is
> too coarse to trust — "absent by patch-id" lumps together *genuinely needs it*,
> *not applicable (code isn't there)*, *already fixed differently*, and *diverged by
> design*. Worst of all it can't see that some shared-**looking** code is **client-bound**
> (packet/opcode/`SMSG`-`CMSG` layouts differ per WoW client build) and must **never** be
> ported across cores even when the text merges. This spec replaces the binary model with
> a **per-(fix × core) verdict** decided by *actually applying the patch to that core's
> current code* (`git apply --check` against a per-core worktree) **and** gating the
> confident recommendation behind a **relevance** classifier that knows shared-infra from
> divergent-by-design. The hard guarantee: **a "NEEDS — port this to core X" is only ever
> produced when git proves a clean apply AND the change is genuinely portable shared code.
> The board never recommends porting something that differs client-to-client or by
> expansion.** Everything uncertain goes to a labeled **REVIEW** lane; nothing is silently
> hidden.

<!-- Follows the docs/specs/ numbered-section convention. Terse; tables/bullets over prose. -->

## 1. Summary

The Port-Verdict Engine turns "which cores need this fix?" into a *proven, relevance-checked*
answer. For each fix (`PatchGroup`) and each core, it produces one of four verdicts —
**HAS IT / NEEDS / REVIEW / N/A** — by combining three signals: (1) `patch-id` presence
(exact, as today); (2) a **real git apply-check** of the patch against that core's HEAD (the
authoritative "does it physically fit, is it already there, is the code even present?"); and
(3) a **relevance class** for the touched code (`portable-shared` vs `divergent-by-design`,
where divergent-by-design covers both expansion content *and* client/protocol-bound code such
as packets/opcodes). A confident **NEEDS** requires *both* a clean apply *and* portable-shared
relevance; anything else that might still matter is **REVIEW**; structurally-absent code is
**N/A**. The board becomes **one card per fix** showing its cross-core verdict matrix, with a
"needs porting to [core]" filter. The maintainers are the authority on the portable/divergent
line — seeded from paths + drift data, tunable by manual override, learnable from dismissals.
Audience: getMaNGOS maintainers (Antz, MadMax) + r-log. The engine is **core-count-agnostic**:
it verdicts whatever cores exist, so adding `zero`/`one`/`four` is a data step it then covers.

## 2. Goals & Non-Goals

**Goals**
- **Truthful recommendations.** A "NEEDS" is *never* emitted for code that is client-bound or
  expansion-divergent, even if the patch text merges. NEEDS = clean apply **and** portable-shared.
- **Per-core certainty.** Decide applicability per core by *running git*, not guessing: clean
  apply / already-present / not-applicable / conflict.
- **Catch the four confusions** that "absent by patch-id" hides: needs-it, not-applicable
  (code absent), already-fixed-differently, diverged-by-design.
- **Honest uncertainty.** Conflicts and "applies-but-maybe-not-relevant" go to a visible
  **REVIEW** lane with evidence — never a confident recommend, never silently hidden.
- **One fix → cross-core matrix.** Dedupe the per-target repetition; one card per fix lists the
  cores it needs / should be reviewed for / is N/A for / already has it.
- **Maintainer-authoritative relevance.** Seed the portable/divergent line, but let maintainers
  override per subsystem and have the engine learn from their dismissals.
- **Core-agnostic + incremental.** Works for any set of cores; a `(fix, core)` verdict recomputes
  only when the fix is new or that core's HEAD moved.

**Non-Goals**
- **No semantic/AST equivalence.** We do not prove "this core's content exercises this path."
  Relevance is a path/heuristic + maintainer judgment, and uncertainty is surfaced as REVIEW.
- **No automatic porting / write-back.** The engine recommends and grades; humans port.
- **No retirement of `patch-id`.** It remains the fast present-set signal and the `HAS IT` source.
- **No new core content rules.** Expansion divergence is data (classifier + overrides), not
  hard-coded gameplay knowledge.
- **Not a replacement for the board/auth layers.** Consumes the existing gated board (B1/B2/B3);
  changes the *export shape*, not the login or board-state machinery.

## 3. Context & Constraints

- **What exists (verified by reading the code, 2026-06-22):**
  - `sync/propagate.py` builds the `(patch_id, core)` present/absent matrix from `Commit`/`CommitPatch`
    (+ cherry-trailers). Offline. → keep as the present-set + `HAS IT` source.
  - `sync/portcandidates.py` emits one `PortCandidate` per absent target **iff** a touched subsystem
    is classified exactly `shared`; `mixed` is silently skipped (under-coverage); magnitude = portable
    lines. → **replaced** by the verdict stage.
  - `sync/classify.py` classifies a subsystem `shared|expansion|mixed|vendored` by path heuristics
    (`SHARED_PREFIXES`, `EXPANSION_SEGMENTS`, `VENDORED_PREFIXES`), with manual-override preservation.
    → **extended** with `client_bound` and the drift signal.
  - `git/client.py` `LocalGitClient` does bare `--mirror` clones + `patch-id`/numstat. **No worktree,
    no apply.** → **extended** with per-core worktrees + apply-check.
  - The drift module already computes per-subsystem cross-fork divergence (blob-SHA); the real-run
    showed `WorldHandlers/Server fully diverged` across forks — the **fingerprint of client-bound code**.
  - Board (`B1/B2/B3`): `BoardItem` keyed `patch_group_id:target_core`, `/api/board`, `/port/` UI.
- **Domain truth (from the owner, 2026-06-22):** packet sending is **client-bound** — M3 (client 15595)
  and M2 (client 12340) expect different bytes, so an M3 packet change is fundamentally **not** for M2
  even if it merges. The goal is to keep cores aligned on everything the expansion/client delta *allows*
  and deliberately leave the client-bound surface diverged. The tool must flag the former and never nag
  about the latter.
- **Expansion era ordering** (for direction evidence): `zero`(vanilla) < `one`(TBC) < `two`(WotLK) <
  `three`(Cata) < `four`(MoP). A *back-port* (newer→older) of expansion/client content is the classic
  false-recommend.
- **Constraints:** Python 3.12, async SQLAlchemy 2.0, `Fake*` seams, repository seam; offline-first
  where possible (classification, verdict synthesis) with the git-worker the one stateful piece; read-only
  externally; 4-space indent; `feat:`-style commits; **no AI attribution**.

## 4. Invariants (Non-Negotiable Rules)

1. **NEEDS = proven + portable.** A `NEEDS` verdict is emitted **only** when git proves a **clean apply**
   to the target's HEAD **and** the change is in **portable-shared** code. Never on a conflict, never on
   client-bound/expansion-divergent code, never on "the text happened to merge."
2. **Client-bound is never port-debt.** Packet/opcode/`SMSG`/`CMSG`/byte-layout code is `divergent_by_design`
   and can never become NEEDS, regardless of apply result.
3. **Honest uncertainty, nothing hidden.** Conflicts and clean-applies-into-divergent-content become
   **REVIEW** with evidence; structurally-absent code becomes **N/A** with a reason. Both stay visible.
4. **Git vouches for applicability.** "Does it fit / is it already there / is the code present?" is answered
   by `git apply --check` (+ `--reverse`) against a real worktree, not by a path guess.
5. **Maintainer is the authority on relevance.** The portable/divergent line is seeded but always overridable
   per subsystem; a manual override wins over heuristics and persists across recompute.
6. **Derived & recomputable; raw untouched.** `PortVerdict` is derived from `Commit*` + git worktrees +
   `SubsystemClass`; it may be wiped and rebuilt. Human board state (`BoardItem`) is separate and durable.
7. **Commit-anchored & incremental.** Every verdict records the `source_sha` tested and the target's
   `base_sha`; it recomputes only when either moves.
8. **Core-agnostic.** No verdict logic hard-codes the set or count of cores; it operates over whatever
   cores have harvested commits.

## 5. System Architecture

```
  Commit/CommitPatch (raw)        SubsystemClass (relevance)        git worktrees (per core, at HEAD)
        │ patch-id present-set          │ portable | divergent          │ apply --check / --reverse
        ▼                               ▼  (shared) (expansion|client)   ▼
   propagation (exists) ─────►┌──────────────────────────────────────────────────────┐
   classify (extended) ──────►│  VERDICT STAGE  (new, sync/verdicts.py)               │
   drift fully-diverged ─────►│   for each fix × each non-present core:               │
   manual overrides ─────────►│     paths_exist? ─ reverse-apply? ─ forward-apply? ─┐ │
                              │     + relevance class + similar-work + direction    │ │
                              │     ──► PortVerdict {has_it|needs|review|n/a}        │ │
                              └──────────────────────────────────────────────────────┘
                                                   │ needs + review = actionable
                                                   ▼
                              publish: board JSON grouped BY FIX (cross-core matrix)
                                                   │
                                                   ▼
                              /api/board + /port/  (one card per fix, filter by core)
```

- **Truth layers:** `Commit*` + worktrees = code truth; `SubsystemClass` = relevance truth (heuristic +
  manual); `PortVerdict` = derived verdict; `BoardItem` = human intent (unchanged).
- **The git-worker gains a worktree-per-core** (checked out at HEAD, refreshed on fetch) — the one new
  stateful capability, the only way to run a real `git apply`.
- **Offline parts stay offline:** classification and verdict synthesis read the DB + worktrees; no network.

## 6. Data Model

### 6.1 Relevance (extends `SubsystemClass`)
`classification ∈ {shared, expansion, client_bound, vendored, mixed}`; **portable ⇔ `shared`**.
`divergent_by_design ⇔ expansion | client_bound | vendored`. `mixed` resolves at file granularity.
- `source ∈ {seed, heuristic, drift, ai, manual_override}`; `manual_override` always wins (Invariant 5).
- **Seeds:** `CLIENT_BOUND` paths (opcode tables, `SMSG_*`/`CMSG_*` packet classes, WorldHandlers/Server
  packet surface, auth packet layouts); existing `EXPANSION_SEGMENTS`; `VENDORED_PREFIXES`; `SHARED_PREFIXES`
  minus anything reclassified client-bound. **Drift signal:** a subsystem *fully diverged across all core
  pairs* is auto-seeded `client_bound` (the WorldHandlers/Server fingerprint), unless overridden.

### 6.2 `PortVerdict` (new — derived, recomputable)

| Field | Meaning |
|---|---|
| key `(patch_group_id, core)` | one verdict per fix per core |
| `verdict` | `has_it | needs | review | not_applicable` |
| `apply_result` | `present | reverse_clean | clean | conflict | file_absent` (the git fact) |
| `relevance` | `portable | divergent | mixed` (resolved; mixed→file-level outcome recorded in evidence) |
| `source_core`, `source_sha` | the fix's canonical source (min present core) whose patch was tested |
| `base_sha` | the target core's HEAD the patch was tested against (temporal anchor) |
| `subsystem`, `magnitude`, `tier` | portable-line magnitude + tier (surgical/small/moderate/bulk) |
| `confidence` | `high` (clean+portable) / `medium` (review w/ corroboration) / `low` |
| `similar_commit` | nullable — a target commit touching the same files w/ overlapping title ("maybe already fixed differently") |
| `evidence[]` | apply result, relevance + reason, direction note, similar-work hint |
| `computed_at` | recompute stamp |

- **`PortCandidate` is superseded** by the `needs|review` subset of `PortVerdict`. (Migration: drop or
  view-map; the board export reads `PortVerdict`.)
- **`BoardItem`** unchanged (`patch_group_id:target_core`) — each `needs`/`review` core is independently
  claimable; the card groups by fix. `has_it`/`not_applicable` cores are not claimable.

### 6.3 Verdict decision (the gate)

For a fix with present-set `P` (patch-id), canonical `source = min(P)`, its patch `D = diff(source_sha)`,
touched files `F`; for each core `c ∉ P`, against `c`'s worktree at `base_sha`:

| Apply result | Relevance of touched code | **Verdict** |
|---|---|---|
| reverse-applies (change already there) | any | **HAS IT** |
| `git apply --check` clean | `portable` (or portable files in `mixed`) | **NEEDS** ✅ |
| `git apply --check` clean | `divergent` (expansion/client_bound/vendored) | **REVIEW** ("applies, but area differs by design — verify") |
| file(s) of `D` absent on `c` | any | **N/A** ("code not present") |
| conflict (region diverged) | any | **REVIEW** ("diverged — adapt or already-fixed-differently"; client_bound adds "likely don't port") |

Cores in `P` are **HAS IT** (source/exact). Direction (`source` newer than `c`) + `similar_commit` are
attached as evidence on REVIEW.

## 7. Pipeline & Data Flow

The `sync-analyze` chain gains a **verdict stage** (replacing port-candidates):
1. **Propagation** (existing) — present-set per `patch_id`.
2. **Classify** (extended) — subsystem → `{shared|expansion|client_bound|vendored|mixed}`, seeded incl.
   client-bound paths + the drift fully-diverged signal; manual overrides preserved.
3. **Verdicts** (new, `sync/verdicts.py`) — for each fix, for each `c ∉ present-set`:
   `paths_exist(c, F)` → `apply_check(c, D, reverse=True)` → `apply_check(c, D)` → grade with relevance →
   upsert `PortVerdict`. Cached on `(patch_group_id, core, source_sha, base_sha)`; recompute only on change.
4. **Reconcile board** — a `BoardItem` whose `(fix, core)` is no longer `needs|review` (now `has_it`/`n/a`)
   auto-archives (existing reconcile, repointed at `PortVerdict`).
5. **Publish** — board JSON **grouped by fix**, each with its cross-core matrix.

- **Worktrees:** `ensure_worktree(core)` checks out the bare mirror at HEAD into `worktrees/<core>`; refreshed
  after each fetch. Apply-checks run there; `--check` mutates nothing.
- **Cost:** ≈ |fixes| × |cores not present| apply-checks per full build (each ms-scale); incremental after.

## 8. UX — `/port/` (re-modeled)

- **One card per fix:** title · source core(s) · subsystem · magnitude(tier), then the **verdict matrix**:
  - ✅ **NEEDS →** [cores] — each independently **claimable/assignable** (the confident worklist).
  - ⚠️ **REVIEW →** [cores] — claimable; each shows *why* (conflict / divergent-by-design / similar-work hint).
  - ⊘ **N/A →** [cores] — shown with reason ("code not present"), not claimable (proves we checked).
  - ✓ **HAS IT →** [cores] — informational.
- **Filter "needs porting to [core]"** → any single core's confident worklist (replaces per-core columns).
- Existing views/filters (My ports, By person, tier/subsystem/source/search) operate over the per-fix cards.
- **Evidence on expand:** the apply result, relevance + reason, direction, and any similar-commit pointer.
- Dismiss (maintainer) optionally offers **"mark `<subsystem>` divergent-by-design"** → a `manual_override`
  so the whole area stops being recommended (the learning loop, Phase 5).

## 9. Interfaces & Contracts

- **`GitClient`** (extended) — new: `ensure_worktree(core) -> path`; `apply_check(core, patch_text, *,
  reverse=False) -> ApplyResult`; `paths_exist(core, paths) -> dict[str,bool]`; `diff(core, sha) -> str`
  (the patch). `ApplyResult ∈ {clean, conflict, file_absent, reverse_clean}` (`reverse_clean` = the patch
  reverse-applies, i.e. the change is already present). `FakeGitClient` gains scripted
  apply results for hermetic tests.
- **`classify_subsystem`** (extended) — adds `client_bound` (paths) + a drift-signal hook; same manual-override
  contract.
- **`compute_verdicts(session, git_client) -> dict`** (new) — drives the verdict stage; returns counts
  `{needs, review, not_applicable, has_it, cached, recomputed}`.
- **`PortVerdictRepository`** (new) — `upsert` (status-agnostic; derived), `actionable()` (`needs|review`),
  `for_fix(patch_group_id)`.
- **Board export** — `build_port_verdicts(session)` groups `needs|review` by `patch_group_id` into per-fix
  cards (`{id, title, source_core, subsystem, tier, magnitude, needs:[…], review:[{core,reason}…],
  na:[{core,reason}…], has_it:[…], board-overlay per (fix,core)}`). Replaces `build_port_candidates`.
- **Validation harness** — golden fixtures (§12) drive `FakeGitClient`; a `LocalGitClient` integration test
  proves real apply/reverse/absent/conflict on crafted repos.

## 10. Security & Access

Unchanged from the board specs: read-only externally; the engine only reads git + writes Mai's own DB;
the board stays behind the login gate. Worktrees hold public-repo source only; `worktrees/` is gitignored.

## 11. Edge Cases & Failure Modes

| # | Case | Handling |
|---|------|----------|
| 1 | Code exists, fix applies clean, but only matters for newer expansion | relevance gate: `divergent` ⇒ **REVIEW**, not NEEDS (clean apply alone never graduates divergent code). |
| 2 | Client-bound packet change (M3→M2) that text-merges | `client_bound` ⇒ **REVIEW** (or excluded w/ reason); **never NEEDS** (Invariant 2). |
| 3 | MoP-only system absent in vanilla | files absent ⇒ **N/A** ("code not present"). |
| 4 | Already fixed differently (different diff) | reverse-applies ⇒ **HAS IT**; else conflict ⇒ **REVIEW** + `similar_commit` hint. Never a false NEEDS. |
| 5 | Diverged region (needs adaptation) | conflict ⇒ **REVIEW** ("diverged — adapt"). |
| 6 | Patch touches both shared & client-bound files (`mixed`) | resolve per file: portable files ⇒ NEEDS-eligible; client-bound/expansion files ⇒ REVIEW; magnitude counts portable lines only. |
| 7 | Bare mirror can't apply | maintain a **worktree per core** at HEAD; refresh on fetch; `--check` only. |
| 8 | Binary / huge / rename in patch | rename-following on the diff; binary hunks → can't `--check` cleanly ⇒ **REVIEW** ("binary/blob change — verify"). |
| 9 | Target HEAD force-pushed / moved | verdict keyed on `base_sha`; stale verdicts recompute when HEAD changes. |
| 10 | Merge commit as source | no single patch-id (existing) ⇒ excluded as a fix source. |
| 11 | Misclassified subsystem (false NEEDS slips) | maintainer `manual_override` reclassifies the subsystem → recompute drops it; optional dismiss-to-override learning loop. |
| 12 | Drift signal mislabels a genuinely-shared subsystem `client_bound` | seed is overridable; conservative — a wrong `client_bound` only *demotes* to REVIEW (visible), never hides as NEEDS. |

## 12. Validation — proving the recommendations are truthful

Gates before the board trusts the verdicts (drive `FakeGitClient`; one real-git integration suite):
1. **Shared fix → NEEDS.** A staged shared-infra fix absent in a sibling, applies clean ⇒ **NEEDS**.
2. **Client-bound never NEEDS.** A packet/opcode change that *applies cleanly* to another core ⇒ **REVIEW**,
   never NEEDS (the core truthfulness gate, Invariant 2). Non-vacuous: assert it would have been NEEDS under
   pure apply, and the relevance gate held it back.
3. **Expansion-absent → N/A.** A newer-expansion-only file ⇒ **N/A** on the older core.
4. **Already-present (reverse) → HAS IT.** A fix whose effect is already in the target via a different commit
   ⇒ reverse-applies ⇒ **HAS IT**, not NEEDS.
5. **Conflict → REVIEW.** A diverged region ⇒ **REVIEW**, with the similar-work hint when a matching target
   commit exists.
6. **Determinism + incrementality.** Same `(source_sha, base_sha)` ⇒ same verdict; unchanged inputs ⇒ cache hit.
7. **Real-git integration.** Crafted repos exercise clean / reverse / file-absent / conflict end-to-end.
8. **Manual spot-audit gate.** A sample of NEEDS verdicts hand-verified against the real forks before the
   board surfaces them as confident recommendations.

## 13. Phased Build Plan

Each phase is independently shippable; the board keeps working throughout.
- **P1 — Relevance v2.** Extend the classifier with `client_bound` (paths) + the drift fully-diverged seed;
  keep manual-override preservation. Offline, cheap. → the portable/divergent line exists and is tunable.
- **P2 — Git-worker apply capability.** `ensure_worktree` + `apply_check`(+reverse) + `paths_exist` + `diff`
  on `LocalGitClient`; `FakeGitClient` scripted results; worktrees gitignored. → git can answer applicability.
- **P3 — Verdict stage.** `PortVerdict` model + repo + `compute_verdicts` (apply × relevance gate) +
  incremental cache; **§12 validation gates 1–7**. Replace `compute_port_candidates`/`PortCandidate`. → the
  trustworthy per-(fix,core) verdict exists. **← MVP: recommendations are truthful.**
- **P4 — Board re-model.** `build_port_verdicts` (per-fix cards + matrix); `/port/` UI = one card per fix +
  needs/review/na/has-it + "needs porting to [core]" filter; reconcile repointed at `PortVerdict`. → the cockpit
  shows the truthful model.
- **P5 — Learning loop (optional).** Dismiss-to-`manual_override`; periodic real-repo spot-audit (§12.8).

**Prerequisite (data, not logic):** clone `zero`/`one`/`four` (GitHub URLs in the registry) so the engine has
worktrees to verdict against; the logic is core-agnostic and covers them automatically.

## 14. Open Questions & Risks

| # | Item | Owner |
|---|------|-------|
| 1 | Exact `CLIENT_BOUND` seed paths for the 5 forks (opcode/packet surface) — confirm with Antz/MadMax. | r-log / Antz |
| 2 | Drift "fully diverged across all pairs" threshold for auto-`client_bound` seeding (all pairs? a majority?). | r-log |
| 3 | Worktree disk cost for 5 full forks; shallow checkouts vs full; refresh cadence. | r-log |
| 4 | `mixed` file-level resolution: per-file relevance lookup cost vs a coarser subsystem call. | r-log |
| 5 | `similar_commit` detection method (file overlap + title token similarity) + its false-positive rate. | r-log |
| 6 | Whether P5 dismiss-to-override is automatic or a one-click maintainer confirm (lean: confirm). | r-log / Antz |
| 7 | Migration of existing `PortCandidate` rows/board items to `PortVerdict` keys (same `patch_group:core` key → low risk). | r-log |

## 15. Glossary & References

- **Verdict** — per `(fix, core)`: `HAS IT | NEEDS | REVIEW | N/A`.
- **NEEDS** — the only confident "port this": clean git apply **and** portable-shared relevance.
- **REVIEW** — visible-but-uncertain: clean-into-divergent, conflict, binary, or similar-work-found.
- **N/A** — the code the patch touches isn't present on that core (structural divergence).
- **portable-shared** — infra both clients use the same way (auth, DB, threading, extractors, shared logic).
- **divergent-by-design** — expansion content **or** client/protocol-bound code (packets/opcodes/`SMSG`/`CMSG`);
  never NEEDS.
- **client-bound** — code whose behavior is fixed by the WoW client build (15595 vs 12340 …); divergent by design.
- **apply-check** — `git apply --check` (+ `--reverse`) of a fix's diff against a core's worktree at HEAD.
- Builds on / amends: `sync-intelligence-engine.md` (replaces its `PortCandidate` model with `PortVerdict`),
  feeds `port-debt-board-multiuser.md` (board export shape changes; auth/board-state unchanged).
- Owner domain input (2026-06-22): packet sending is client-bound (15595 vs 12340) → never cross-port;
  keep cores synced on what the expansion/client delta allows, leave client-bound diverged.
