---
title: "Mai — Sync Intelligence Engine"
status: Draft
version: 0.1
owners: [r-log]
related:
  - framework-architecture.md
  - 07-drift-observatory.md
  - 02-github-harvester.md
  - dashboard-workspace-redesign.md
---

# Mai — Sync Intelligence Engine

> Mai's drift signal today is a **byte-identity heatmap** (GitHub Trees blob-SHA): it
> says *which files differ*, not *what needs porting*. That is too coarse to trust —
> it can't tell design-divergence (Cata vs WotLK, by design) from a missing fix, has
> no magnitude, and can never confirm a specific fix propagated. This spec replaces it
> with a **commit/patch-level cross-fork measurement engine** (fidelity rung **L3**):
> a stateful **git-worker** keeps bare clones of the forks and uses git's own
> `patch-id` / cherry detection to answer *"has fix X landed in the sibling, or is it
> port-debt?"* with **confidence + evidence**, kept fresh automatically. Mai is
> delivered as a **read-only GitHub App** so it self-updates once installed. The
> planning board is a **later, separate spec** that consumes this engine's output.

<!--
  Follows the docs/specs/ numbered-section convention. Terse prose, tables/bullets
  over paragraphs, every claim concrete. Section numbers are stable ("see §6.4").
-->

## 1. Summary

The Sync Intelligence Engine is the trustworthy foundation under everything Mai wants
to be. It ingests **code truth** (commits, diffs, `git patch-id`) from each getMaNGOS
fork via a git-worker, computes **cross-fork fix propagation** (which forks contain
each patch), classifies each subsystem as **shared-infrastructure vs expansion-content**
so by-design divergence stops masquerading as work, and emits two trustworthy outputs:
**SyncObservation** (accurate, magnitude-weighted, temporal divergence per subsystem/pair)
and **PortCandidate** (a *specific fix* present in fork A, absent in B, in a portable
subsystem — each carrying a confidence level and the evidence behind it). It runs as a
**read-only GitHub App** that refreshes on App webhooks (when installed) with a cron
backstop. Audience: getMaNGOS maintainers (Antz, MadMax) and r-log. It is the literal
job Antz does by hand today — finding which sibling-fork fixes still need carrying over —
turned into a measured, continuously-updated, auditable backlog.

## 2. Goals & Non-Goals

**Goals**
- Measure cross-fork sync at **commit/patch level (L3)**, not file-state, so "what needs
  porting" is *proven* (patch-id / cherry), not *inferred*.
- **Denoise** port-debt with a `shared | expansion | mixed` subsystem classifier — Cata-vs-WotLK
  design divergence is recorded as "expected delta," never flagged as work.
- Add **magnitude** (added/removed lines) and **rename-following** so cosmetic and moved-file
  differences stop reading as divergence (closes the L1 holes).
- Make every claim **auditable**: each PortCandidate carries `confidence` + an evidence list
  of which signals fired. Show our work; never assert a match we can't prove.
- **Self-update**: incremental, idempotent refresh cycle driven by a pluggable trigger
  (App webhook + cron backstop), recording commit-anchored, **temporal** observations.
- Ship as a **read-only GitHub App**: testable on r-log's own forks now, install-target-agnostic
  so Antz installs the identical App on getMaNGOS later with zero rework.
- **Prove correctness** with deterministic fixtures (a crafted cherry-pick on owned forks,
  a known expansion file, a squash-merge case) before any consumer trusts the data.

**Non-Goals**
- **No planning board here.** Boards/cards/assignment/multi-user auth are a separate downstream
  spec (`sync-planning-board.md`, TBD) that *consumes* `PortCandidate` / `SyncObservation`.
- **No write-back to GitHub/IPS.** The App is read-only; Mai still writes nothing external.
- **No L4 semantic/AST analysis.** Confidence + evidence is the honesty mechanism, not AST equivalence.
- **Not retiring the existing pipeline.** Enrich/embed/correlate/verdict/publish are extended,
  not replaced; blob-SHA drift is demoted to a cross-check, not deleted.
- **No automatic porting.** Mai recommends and tracks; humans port. (Write-back is a far-future,
  separately-gated phase.)

## 3. Context & Constraints

- **What exists (verified by reading the code, 2026-06-17):**
  - `drift/compare.py` `compare_trees` is **pure blob-SHA**: per co-located file, equal SHA →
    `identical`, any difference → `diverged`; present-in-one → `only_a/only_b`. "Drift %" =
    `diverged/shared`. Correct for what it computes; the wrong instrument for port-debt.
  - `drift/client.py` `GitHubTreeClient` fetches `/repos/{repo}/git/trees/HEAD?recursive=1`
    blob-only — **no commit history, no diffs**. `ref="HEAD"` is unanchored (observations
    aren't tied to a reproducible commit SHA).
  - `harvest.py` ingests **issues + pulls metadata** only (`normalize_issue/pull`); there is
    **no `list_commits`** and no `gh_commit` source type. Source types in the live DB:
    `gh_issue`, `gh_pr`, `ips`.
- **The plan series already deferred exactly the missing pieces:** Plan 02b (commits-harvest)
  and Plan 07's "line-count deltas" + "sync-commit parsing." This spec is those deferrals,
  done properly and unified.
- **Forks diverge by design.** Per workspace `CLAUDE.md`: expansion content (Cata 4.3.4 vs
  WotLK 3.3.5a) differs deliberately; **shared infrastructure** (logging, networking, DB,
  threading) should align. The classifier seeds from this knowledge.
- **Ownership reality.** r-log does **not** admin the getMaNGOS fork repos, so plain webhooks /
  added Actions are not installable there. r-log **does** own `r-log/server`,
  `r-log/server-two`, and `r-log/Mai` — the dev/test bed. The GitHub App model resolves this:
  install on owned forks now; Antz installs on getMaNGOS later.
- **Architectural reversal (accepted by owner, 2026-06-17):** L3 reverses Plan 07's deliberate
  "no git clone, stay serverless" choice. A **stateful git-worker with a disk volume** is
  required; `git patch-id` is the battle-tested tool for "is this patch applied," and the
  clones double as the source for line-magnitude and rename-following.
- **Hard constraints:** read-only externally; Python (match existing stack: async SQLAlchemy 2.0,
  httpx, pydantic-settings, pytest, `Fake*` protocol seams); repository seam keeps the store
  swappable (SQLite local → Neon deploy); `.env` holds live secrets, never committed.

## 4. Invariants (Non-Negotiable Rules)

1. **Read-only externally.** The App requests only `*:read` scopes; Mai writes nothing to GitHub/IPS.
2. **Raw is append-only & immutable; everything else is recomputable.** `Commit*` rows are raw
   code-truth; all `PatchGroup / Propagation / SyncObservation / PortCandidate` data is derived
   and may be wiped and rebuilt from raw + git mirrors.
3. **Proven over inferred, and always labeled.** A propagation/port claim states its `confidence`
   (`high` = patch-id or cherry-trail; `medium` = aggregate/similarity inference) and lists the
   evidence. The engine never emits a bare yes/no it cannot defend.
4. **Design-divergence is not work.** Expansion-class divergence is recorded as "expected delta"
   and is **never** surfaced as port-debt. Only `shared`-class, un-propagated fixes graduate.
5. **Commit-anchored & temporal.** Every observation records the base commit SHA of each fork and
   an `observed_at`; sync is a time series, never a single mutable "current" number.
6. **Trigger-agnostic, self-reconciling.** The refresh cycle is identical regardless of trigger;
   cron is always the backstop so dropped webhooks cannot leave data stale.
7. **Git vouches, we don't approximate.** Patch identity comes from `git patch-id --stable` and
   `git cherry`, not a hand-rolled diff hash (which exists only as a labeled `medium`-confidence
   fallback, never as the primary signal).
8. **Install-target-agnostic.** No code path hard-codes r-log's forks or getMaNGOS's; the tracked
   repo set is data (the registry), so the same App serves both installs.

## 5. System Architecture

Two ingestion paths feed one derivation layer. **API harvester = narrative** (PR/issue text,
already built). **Git-worker = code truth** (commits, diffs, patch-ids). They **join on commit SHA**.

```
  GitHub App (read-only install on forks)
     │  webhook: push / pull_request           │  installation token (read, higher rate limit)
     ▼                                          ▼
  Trigger seam ───────────────┐         API harvester (existing)
   • WebhookTrigger (App)      │          issues + pulls → Report (gh_issue/gh_pr)
   • CronTrigger (backstop)    │                       │
     │ fires refresh cycle     │                       │ join on commit SHA
     ▼                         ▼                       ▼
  Git-worker (stateful, disk volume)            ┌───────────────────────────┐
   mirrors/<core>.git  (bare clones)            │  Derivation layer (pure)  │
     git fetch  →  new commits since cursor  →  │  PatchGroup  (patch-id)   │
   Git harvester:                               │  Propagation (which forks)│
     per commit: sha, author, dates, parents,   │  SubsystemClass           │
     touched files (+lines), git patch-id  ───► │  SyncObservation (temporal)│
                                                │  PortCandidate (+conf/evid)│
   Blob-SHA drift (kept) ──── cross-check ─────►└───────────────────────────┘
                                                          │
                                  extend existing: enrich · embed(fixes too) · correlate · verdict
                                                          │  publish
                                                          ▼
                            mai-data/  (data/*.json + content/*.md)  → Hugo → site
                            (consumed later by the planning board, separate spec)
```

- **Data topology (where truth lives):** raw `Commit*` + git mirrors are authoritative code-truth;
  everything else is derived and rebuildable. Store is SQLite locally, Neon in deploy (repo seam).
- **Presentation topology:** unchanged for now — the engine writes JSON/`.md`; the existing Hugo
  site renders it. The board (future spec) is the richer consumer.
- **Server side** is Python throughout; the **git-worker** is the one new long-lived component
  (needs a filesystem; not a pure edge function).

## 6. Data Model

### 6.1 Raw — code truth (append-only, immutable)

| Entity | Key | Fields |
|---|---|---|
| **Commit** | `(core, sha)` | author, authored_at, committer, committed_at, message, parent_shas[], is_merge |
| **CommitFile** | `(commit, path)` | change_type (A/M/D/R), old_path (rename), added_lines, removed_lines, subsystem (derived at depth-3) |
| **CommitPatch** | `commit` | `patch_id` (`git patch-id --stable`, null for merges), `normalized_hash` (whitespace-insensitive fallback), `aggregate_of` (set for a PR-level synthetic patch) |

### 6.2 Derived — recomputable (never authored)

| Entity | Key | Meaning |
|---|---|---|
| **PatchGroup** | `patch_id` | A canonical *fix identity*; members are the commits across forks sharing it. |
| **Propagation** | `(patch_group, core)` | `present | absent`, `via` ∈ {native, cherry_pick, squash_match, inferred}, `confidence`. The "which forks have this fix" matrix. |
| **SubsystemClass** | `subsystem` | `class` ∈ {shared, expansion, mixed}, `source` ∈ {seed, heuristic, ai, manual_override}. |
| **SyncObservation** | `(core_a, core_b, subsystem, observed_at)` | base_sha_a, base_sha_b, identical/diverged/only_a/only_b, `lines_diverged`, `class`. Commit-anchored, temporal. |
| **PortCandidate** | `(patch_group, source_core, target_core)` | subsystem, class, magnitude, `confidence`, `evidence[]`, `status` ∈ {open, ported, dismissed}, related_pr, related_bug. **The trustworthy seed.** |

### 6.3 Identity, provenance, temporal

- **Fix atom = `git patch-id --stable`** over a non-merge commit's diff: whitespace-canonical,
  line-number-independent. The same change applied at different points yields the same id.
- **PR-aggregate patch** (`CommitPatch.aggregate_of`) = a patch-id over a PR's `base...head`
  combined diff, to bridge squash-vs-multi-commit (see §11).
- **Joins:** `Commit.sha` ↔ a `gh_pr` `Report` via the PR's merge/head SHA, so code truth inherits
  the PR's enrichment, correlation, and verdict. `PortCandidate.related_bug` rides existing correlation.
- **Temporal:** each refresh stamps new `SyncObservation` rows (never updates in place), so divergence
  is a trend ("diverging faster this month") and "what changed since you last looked" is queryable.

### 6.4 Subsystem classification seed

Seed `SubsystemClass` from `CLAUDE.md` knowledge + path heuristics, then refine:
- **shared:** `src/shared/**`, `dep/**`, `src/game/Server/**` (networking), logging, DB layer, threading.
- **expansion:** spell/talent/quest/raid content, DBC-schema-bound code, opcode tables.
- **mixed:** a subsystem with both; resolved at **file** granularity (a fix in a shared file inside a
  mixed subsystem still counts as shared). `ai` source = the enricher's per-fix portability tag;
  `manual_override` always wins.

## 7. Pipeline & Data Flow

The **refresh cycle** (one function, any trigger). Every stage is cursor-gated and idempotent:

1. **Fetch** — `git fetch` each bare mirror (scoped to the changed fork when a webhook names it,
   else all). New commits = since the per-fork commit cursor.
2. **Git-harvest** — for *new* commits only: compute `patch-id`, diffstat, touched files/renames →
   append `Commit / CommitFile / CommitPatch`.
3. **API-harvest** — new PRs/issues since cursor (existing machinery), for narrative + verdicts.
4. **Re-derive affected slices only** — update `PatchGroup`/`Propagation` for touched patch-ids;
   classify any new subsystems; re-snapshot affected `(pair, subsystem)` into fresh
   `SyncObservation` rows; regenerate `PortCandidate`s.
5. **Enrich + embed only new/changed** reports **and fixes** (idempotent content-hash skip already
   exists — controls cost; embedding fixes is what lets the AI reason over real propagation).
6. **Recompute** correlation / verdict / seed-items; **publish** JSON + `.md`; refresh.

- **Triggers:** `WebhookTrigger` (App `push`/`pull_request`, debounced/coalesced into one run) +
  `CronTrigger` (reconciling backstop, always on). Identical downstream cycle (§Invariant 6).
- **Resumability:** per-fork, per-stage cursors + idempotency → a failed cycle resumes; partial
  progress preserved (matches existing resumable design).
- **Blob-SHA drift** still runs each cycle as a **cross-check** against the git-worker's file view
  (§12.3); disagreement is a bug signal, not a silent overwrite.

## 8. Infrastructure & Deployment

| Concern | Local (now, validate-first) | Deploy (later, gated) |
|---|---|---|
| Git-worker | Runs on this build-host box; mirrors under `mirrors/<core>.git` (gitignored, regenerable) | Container **with a persistent disk volume** (Cloudflare Container/Fly/VM — verify in §14) |
| Trigger | `CronTrigger` loop / manual CLI; webhooks via **smee.io** forwarding to localhost | App `WebhookTrigger` endpoint + Cron backstop |
| Store | SQLite (`mai.db`) | Neon Postgres (+pgvector) behind the repo seam |
| App identity | GitHub App installed on `r-log/server`, `r-log/server-two` | Same App, Antz installs on getMaNGOS org |
| Secrets | `.env` (gitignored): App id, private key, webhook secret, installation id | Platform secret store |

- The git-worker is the one component that **cannot** be a pure edge function (needs a filesystem).
- Cost floor stays near-zero locally; cloud cost is deferred until the L3 data is proven correct (§12).

## 9. Interfaces & Contracts

- **GitHub App** — permissions: `metadata:read`, `contents:read`, `pull_requests:read`,
  `issues:read`. Webhook events: `push`, `pull_request` (+ optional `issues`). **No write scopes.**
- **`TreeClient`** (existing) stays for the blob-SHA cross-check.
- **`GitClient`** (new protocol, mirrors the `Fake*` pattern):
  `fetch(core)`, `new_commits(core, since_sha) -> [CommitMeta]`, `patch_id(core, sha) -> str`,
  `aggregate_patch_id(core, base, head) -> str`, `cherry(core_a, core_b) -> [match]`.
  Implementations: `LocalGitClient` (subprocess over bare clones) + `FakeGitClient` (in-memory fixtures).
- **`Trigger`** (new protocol): `WebhookTrigger`, `CronTrigger`; both call one `run_refresh_cycle()`.
- **Repositories** (new, behind the seam): `CommitRepository`, `PropagationRepository`,
  `SubsystemClassRepository`, `SyncRepository`, `PortCandidateRepository`.
- **Output JSON** (consumed by the site/board): `sync.json` (per-pair/subsystem SyncObservation +
  class + magnitude + trend) and `port_candidates.json` (each with confidence + evidence + links).
  `frequency.json` / `pushes.json` continue, now sourced from the accurate engine.

## 10. Security & Access

- **App private key + webhook secret** live in `.env` / platform secret store, never committed;
  webhook deliveries are HMAC-verified with the secret.
- **Read-only scopes** only; an installation token is short-lived and per-install.
- **Politeness:** git `fetch` (incremental) and scoped API calls; the installation token raises rate
  limits versus a PAT. Webhooks are debounced so a push storm is one cycle, not many.
- **Data posture:** public-repo data only; no PII beyond public commit author metadata.

## 11. Edge Cases & Failure Modes

| # | Trap | Handling |
|---|---|---|
| 1 | **Squash-merge mismatch** (A squashes a PR to 1 commit, B applies 3) — per-commit patch-ids won't match | Also match the **PR-aggregate** patch-id (`base...head`) ↔ squash; if still no match, fall back to file+line-range overlap as **medium**-confidence "probable port," never **high**. |
| 2 | **Rename/move** reads as two uniques in L1 | git `-M` rename-following on the worker; `CommitFile.old_path` recorded. |
| 3 | **Cosmetic/whitespace diff** inflates divergence | `patch-id --stable` ignores whitespace; magnitude (lines) lets consumers filter trivial diffs. |
| 4 | **Design divergence flagged as work** | `shared/expansion` gate (§Invariant 4); expansion shown as "expected delta." |
| 5 | **Cherry-pick without matching patch-id** (manual conflict resolution changed the diff) | `git cherry`/`--cherry-mark` + `(cherry picked from commit …)` trailer parsing as independent signals; any one → at least medium, multiple → high. |
| 6 | **Merge commits** have no single patch-id | `patch_id = null`, excluded from PatchGroup matching; handled via their constituent commits. |
| 7 | **Truncated/huge fetch** or first-clone cost | Bare mirror is fetched incrementally after the initial clone; initial clone is a one-time cost per fork. |
| 8 | **Dropped webhook** leaves data stale | Cron backstop reconciles every interval regardless of webhook delivery. |
| 9 | **Force-push / rebased history** on a fork | Cursor stored as a SHA; on divergence, re-walk from merge-base; raw commits are append-only (old SHAs retained). |
| 10 | **Install-target differences** (r-log forks vs getMaNGOS) | Tracked repos come from the registry (data), not code; installation id flows from the webhook. |
| 11 | **False "ported"** because B fixed the same bug differently | Reported only at the confidence the evidence supports; differing diffs → not a patch-id match → stays port-debt unless cherry-trail/aggregate proves otherwise. We do not over-claim. |

## 12. Validation — proving the data is correct

Correctness is a deliverable, not an afterthought (matches the workspace "audits are discovery
maps; verify before asserting" discipline). Gates before any consumer trusts the output:

1. **Golden cherry-pick (deterministic, on owned forks).** Craft a known commit in `r-log/server`,
   cherry-pick it into `r-log/server-two`; assert the engine marks it `present` via **both**
   patch-id and cherry-trail at **high** confidence.
2. **Design-divergence anchor.** A known Cata-specific expansion file must classify `expansion` and
   **never** appear as port-debt; a staged shared-infra fix missing in a sibling must appear as
   port-debt, high confidence.
3. **Squash-merge fixture.** Synthetic repo: a PR squashed in one fork, multi-commit in another;
   assert the **aggregate** match catches it and labels confidence correctly.
4. **L1 cross-check.** Blob-SHA file counts vs the git-worker's file view (same trees) must agree;
   any disagreement is a tracked bug.
5. **Determinism.** Same commit → same `patch-id` across runs (asserted).
6. **Manual spot-audit gate.** A sample of `high`-confidence PortCandidates is hand-verified against
   the real repos before the board is wired to trust them.
7. **`FakeGitClient`** drives all logic unit tests (no clones); `LocalGitClient` integration tests
   gated behind an opt-in flag/marker.

## 13. Phased Build Plan

Each phase is independently shippable and leaves the existing site working.

- **Phase 1 — Git-worker foundation.** `GitClient` protocol + `LocalGitClient` (bare clones, fetch,
  `new_commits`, `patch_id`) + `FakeGitClient`. `Commit/CommitFile/CommitPatch` tables + repos +
  commit cursor. CLI `commits-harvest`. → code truth lands in the DB; **MVP boundary is the end of
  Phase 2.**
- **Phase 2 — Propagation + classification (the trust core).** `PatchGroup`/`Propagation` matching
  (patch-id + cherry + trailer), squash-aggregate fallback, `SubsystemClass` seed+classifier,
  `PortCandidate` with confidence+evidence. **Validation §12 (1–5,7) is part of this phase.** CLI
  `sync-analyze`. → *trustworthy* port-debt exists and is proven. **← MVP: the data is correct.**
- **Phase 3 — Accurate SyncObservation + outputs.** Magnitude-weighted, class-filtered,
  commit-anchored `SyncObservation`; `sync.json` + `port_candidates.json`; repoint `frequency.json`/
  `pushes.json` to the accurate engine; blob-SHA demoted to cross-check. → the site shows trustworthy sync.
- **Phase 4 — Freshness + GitHub App.** `Trigger` seam (`CronTrigger` + `WebhookTrigger`),
  `run_refresh_cycle` incremental orchestration, App manifest + webhook receiver + HMAC verify,
  smee.io dev forwarding, install on r-log forks, embed fixes. → self-updating on owned forks.
- **Phase 5 — Hand-off readiness.** Manual spot-audit gate (§12.6), docs for Antz to install on
  getMaNGOS, registry entry for the production repos. → approved, install-ready. (Cloud-infra
  deploy and the planning-board spec follow, separately.)

## 14. Open Questions & Risks

| # | Item | Owner |
|---|---|---|
| 1 | Cloud host for a **stateful git-worker with a volume** (Cloudflare Container vs Fly/VM) — verify before deploy; local-first de-risks this. | r-log |
| 2 | First-clone size/time for four full mangos repos; consider shallow-then-deepen or partial clone. | r-log |
| 3 | `mixed`-subsystem file-level classification accuracy; how much to lean on the AI portability tag vs manual overrides. | r-log |
| 4 | PR↔commit SHA join when PRs are squash- or rebase-merged (merge_commit_sha vs head); confirm against real fork data. | r-log |
| 5 | Confidence thresholds (when does "inferred" qualify at all) — tune against the spot-audit sample. | r-log |
| 6 | r-log forks may lag the real getMaNGOS commit activity; supplement deterministic fixtures with periodic real-repo audits. | r-log / Antz |
| 7 | App review/permissions wording so maintainers trust a read-only install. | r-log → Antz |
| 8 | Whether to keep blob-SHA long-term or retire once L3 is proven (currently: keep as cross-check). | r-log |

## 15. Glossary & References

- **L1/L2/L3** — fidelity rungs: blob-SHA file identity / + line-magnitude+renames / + commit-patch propagation.
- **patch-id** — `git patch-id --stable`; a whitespace-canonical hash of a commit's diff. The fix atom.
- **PatchGroup** — all commits across forks sharing a patch-id; one canonical fix.
- **Propagation** — per-fork present/absent for a fix, with method + confidence.
- **PortCandidate** — a fix present in one fork, absent in a sibling, in a portable subsystem; the board seed.
- **SyncObservation** — accurate, magnitude-weighted, commit-anchored, temporal per-subsystem divergence.
- **shared / expansion / mixed** — subsystem class; only `shared` (or shared files in `mixed`) graduates to port-debt.
- **Trigger seam** — pluggable refresh trigger (`WebhookTrigger` + `CronTrigger` backstop).
- **Git-worker** — the stateful component holding bare clones and running git for patch-ids/cherry.
- Builds on: `07-drift-observatory.md` (blob-SHA, now cross-check), `02-github-harvester.md`
  (narrative path), `framework-architecture.md` (seams/invariants), `dashboard-workspace-redesign.md`
  (current consumer). Superseded plan deferrals folded in: 02b (commits-harvest), 07 (line deltas, sync-commit parsing).
- Consumer (future, separate spec): `sync-planning-board.md` — multi-user private/shared boards with
  auto-seeded port-debt/drift/verification/triage cards + manual cards.
