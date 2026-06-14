---
title: "Mai — Framework, Infrastructure & Pipeline"
status: Draft
version: 0.1
owners: [r-log]
related:
  - _spec-template.md
  - "GITA (reference): https://github.com/r-log/GITA"
  - "Repo registry (source of truth): https://github.com/mangos/MaNGOS/blob/master/README.md"
---

# Mai — Framework, Infrastructure & Pipeline

> Mai is a **read-only GitHub App + service** that aggregates bug/issue data and
> cross-core code drift across the entire getMaNGOS ecosystem into a single,
> dev-only dashboard at `r-log.org/mai`. It fetches from GitHub and the
> getmangos.eu bug-tracker, correlates reports against the PRs/commits that fix
> them, computes how far each fork has diverged from its siblings, and publishes
> everything as a fast static site whose underlying data is Markdown — durable,
> diffable, and readable by future agents. GITA is the **reference** for the
> extraction/sorting pipeline; Mai is its own application.

---

## 1. Summary

The getMaNGOS project is a family of sibling WoW-emulator forks (Zero/One/Two/Three,
plus VB and C# variants). Bug knowledge is scattered: a legacy IPS bug-tracker holds
human reports, but the actual fixes live in GitHub PRs/commits — and most cores have
GitHub Issues **disabled**. No one can see, in one place, what is broken, what is
already fixed, or how far the forks have drifted apart.

Mai centralizes this. It continuously ingests three sources into one canonical store,
runs correlation and drift analysis, and renders a unified dashboard restricted to dev
members. v1 is strictly read-only toward external systems; Mai writes only to its own
store, its git ledger, and its site.

## 2. Goals & Non-Goals

**Goals**
- Aggregate bug/issue/report data from **all tracked mangos repos** into one store.
- Correlate tracker bugs ↔ GitHub PRs/commits to surface **likely-resolved** bugs that
  no human has closed yet.
- Track **cross-core drift**: which subsystems/files diverge between forks and by how much.
- Publish a fast, **dev-only** site at `r-log.org/mai`, backed by Markdown so the data is
  human-diffable and model-readable.
- Be **fully replayable**: rebuild the entire DB and site from immutable raw + git ledger.

**Non-Goals (v1)**
- **No writes** to GitHub or getmangos.eu. Write-back (comments, status changes) is a
  designed-for **later phase**, gated behind a trust model.
- **Not** replacing getmangos.eu as the human intake — it stays primary.
- **Not** building the in-game bug-report addon (separate upstream effort; Mai only
  consumes the richer data it will eventually produce).
- **No public access** — restricted to dev members.

## 3. Context & Constraints

**Verified findings (2026-06-14) that shaped the design:**

| Finding | Implication |
|---|---|
| GitHub **Issues are disabled** on most cores (Zero/One/Two have 0 issues). | Real bug history lives in **PRs and commits**, not Issues. The harvester must treat PRs as first-class. |
| `open_issues_count` from the repos API **counts PRs as issues**. | Never trust that counter; use the issue-vs-PR type split. |
| getmangos.eu bug-tracker is **Invision (IPS)**, ~1,783 active + 806 archived ≈ **2,600 records**, stable `rNNNN` IDs, rich fields (Status / Priority / Category / Sub-category / Version). | Crawlable in full; `rNNNN` is a stable key; status workflow is structured. |
| Tracker **placement lies**: active-category bugs can already be `Completed`; status itself lags the actual fix by months. | Resolution state must be **computed** from code evidence, not read from the tracker. |
| Canonical repo list lives at `mangos/MaNGOS/README.md`. | The repo registry is **read from there**, never hardcoded. |
| In-game addon (proposed to MadMax) will feed **structured** reports (NPC, zone, coords) into IPS. | IPS extraction must be **forward-compatible** (store raw JSONB, map known fields on top). |

**Constraints**
- **Infra:** build around **Cloudflare** (domain `r-log.org` is on Cloudflare). Budget is
  available for paid services after verification.
- **Access:** dev-members only → site sits behind **Cloudflare Access**.
- **Reference:** GITA (Python · FastAPI · Postgres+pgvector · ARQ · Tree-sitter ·
  OpenRouter · WRITE_MODE trust gate) is the pattern source, not a dependency.
- **Embeddings:** via **OpenRouter / OpenAI-compatible** (existing credit).
- **Posture:** read-only externally for v1.

## 4. Invariants (Non-Negotiable Rules)

These are the one-way doors. Every implementation choice must respect them.

1. **Immutable identity.** Key only on immutable IDs — IPS `rNNNN`, `(repo, number)`,
   commit SHA. Never key on title/slug/URL. Each canonical `report` has its own synthetic
   ID plus a **mapping table** to all source IDs.
2. **Raw is sacred.** Every fetch is stored **verbatim, append-only, immutable** (R2 +
   index row) with `fetched_at` + content hash. All normalization, correlation, and
   verdicts are **derived tables that can be dropped and recomputed**.
3. **Temporal by default.** Queryable state carries `observed_at` / `valid_from`. Model
   history, not just current state.
4. **One ingestion contract.** Every source normalizes into a single internal intake
   shape; sources are **pluggable adapters**. Adding a source is additive.
5. **Seam + replayability.** All logic goes through a **views/repository layer** (never
   raw SQL), so the store is swappable; and the system is **fully replayable** from raw +
   git ledger into an identical site.

> Corollary: the embedding model + dimension is pinned (1536), but because raw is
> immutable, re-embedding is always a recompute — never a rebuild.

## 5. System Architecture

**Key reframe — data topology ≠ presentation topology.** Data is organized modularly
(per-core) for clean writes and audit; presentation is a **single domain** with per-core
sections.

- **One Mai service**, a single GitHub App with **many installations** (not copies).
- **Two lenses over one spine:** the *Bug Hub* (what's broken / fixed) and the *Drift
  Observatory* (how far forks diverge) read the same canonical store.
- **Three stores, one truth:** Postgres is the operational **brain**; the `mai-data` git
  repo of `.md` is the durable **ledger** + Hugo source; R2 holds **immutable raw**.

```
            ┌──────── ONE Mai service (Python, multi-installed GitHub App) ────────┐
 GitHub  ──►│  adapters → INGEST → raw(R2) + normalize → Postgres (brain+pgvector) │
 IPS     ──►│                                   │                                  │
 (Firecrawl)│                                   ├─ ENRICH: correlation + drift      │
 README  ──►│                                   │          + embeddings (OpenRouter)│
            │                                   ▼                                  │
            │                     PROJECT: DB → .md → git commit (mai-data repo)    │
            └───────────────────────────────────┬──────────────────────────────────┘
                                                 ▼
                       Cloudflare Pages (hugo build) ── behind ── Cloudflare Access
                                                 ▼
                                  r-log.org/mai   (dev-only)
```

**Presentation (single domain):**
- `r-log.org/mai/` — global dashboard (Antz's overview: counts, drift heatmap, activity).
- `r-log.org/mai/<core>/` — per-core bug lists (the "per-core sites" as sections).
- `r-log.org/mai/sync/` — cross-core drift matrix.
- `r-log.org/mai/bugs/<id>/` — individual report with full provenance.

## 6. Data Model

Tables are split into **immutable** (raw) and **derived** (recomputable) tiers.

| Entity | Tier | Purpose |
|---|---|---|
| `repos` | config | Tracked fork universe, parsed from `mangos/MaNGOS` README. |
| `source_record` | **immutable** | One row per raw artifact (tracker bug, GH issue, PR, commit). Verbatim JSONB payload + `fetched_at` + hash. Append-only; edits append a new version. |
| `report` | derived | The **canonical** normalized bug/finding. May be backed by many `source_record`s. |
| `report_source_map` | derived | Canonical-ID ↔ source-ID mapping (the dedup/identity spine). |
| `code_ref` | derived | "report/PR touches file X near line Y in repo R." |
| `correlation` | derived | Typed edges: report⟷PR, report⟷report (dup across cores), bug⟷commit. |
| `verification` | derived | Verdict `{open \| likely-fixed \| fixed-confirmed}` + cited evidence + confidence. |
| `drift_obs` | derived | "Subsystem/file in fork A diverges from fork B by N commits / behind sync point S," temporal. |
| `event` | **immutable** | Append-only change log (status change, retraction, merge) — the temporal backbone. |

**Identity rule (invariant 1) in practice:** a report is born from its first source
record's immutable ID; subsequent sources matching it (by explicit reference, then
subsystem + embedding similarity) attach via `report_source_map`. "Delete a bug" = emit a
`retracted` event; nothing is hard-deleted.

## 7. Pipeline & Data Flow

Four stages; each stage is idempotent and replayable from the previous tier.

1. **Registry** — read `mangos/MaNGOS` README → upsert `repos`. (daily)
2. **Ingest** — pluggable adapters emit into the single intake contract → write `source_record` (raw) → normalize to `report`/`code_ref`:
   - *GitHub harvester* — issues (where enabled), **all PRs**, breadcrumb commits; incremental via per-repo cursor (last SHA / `updated_at`).
   - *IPS crawler (Firecrawl)* — walks the category tree, deep-scrapes `rNNNN` pages; re-scrapes only rows whose last-comment date changed; authed with the getmangos dev account.
3. **Enrich** — correlation engine (explicit refs → subsystem match → embedding similarity) proposes verifications; drift engine clones/fetches forks and computes per-subsystem divergence. Proposes, never overwrites.
4. **Publish** — project affected records to `.md` (versioned front-matter) → commit to `mai-data` → Cloudflare Pages rebuilds Hugo.

**Two clocks:**
- **Push-driven (fast):** GitHub webhook → incremental harvest of the affected repo →
  re-correlate touched reports → regenerate only those `.md` → rebuild.
- **Cron-driven (slow):** daily full IPS re-crawl + full drift recompute (IPS has no
  webhooks; drift changes even when our repos don't).

## 8. Infrastructure & Deployment

**Decision: Path 2 — Cloudflare front + one Python container + managed Postgres.** Chosen
because the value of Mai is the correlation/drift brain, which serverless edge punishes
most (git clones, long jobs), and GITA's proven Python+Postgres patterns transfer directly.

| Mai component | Cloudflare / service | Notes |
|---|---|---|
| Hugo site | **Pages** | Builds from `mai-data` repo on commit; global CDN. |
| Git ledger | **GitHub repo** `mai-data` | Mai commits `.md`; Pages watches it. |
| Dev-only gate | **Access (Zero Trust)** | Sits in front of Pages; no app-level auth code. |
| Webhooks + API | **Workers** | GitHub `push`/`pull_request` intake. |
| Job queue | **Queues** | Needs Workers **Paid** ($5/mo). |
| Schedules | **Cron Triggers** | Daily crawl + drift. |
| Immutable raw | **R2** | Verbatim scrapes/diffs (free tier 10 GB). |
| Cursors / sessions / cooldowns | **KV** | Last SHA, IPS session cookie, rate gates. |
| **Brain** | **Postgres + pgvector** (Neon via **Hyperdrive**) | Single store: relational + vectors. |
| **Compute** | **Cloudflare Container** (fallback: $5 VPS) | Python FastAPI + worker; git-heavy drift. |
| Embeddings | **OpenRouter / OpenAI-compatible** | Existing credit; 1536-dim; re-embeddable. |

**Cost floor:** ~$5/mo (Workers Paid) + ~$0–19/mo (Neon, free tier likely covers MVP);
everything else fits free tiers at our scale (~2,600 bugs + tens of thousands of PRs).

**To verify in the Cloudflare dashboard (see §13):** Workers Paid, Queues, Containers
availability, R2, Hyperdrive; plus a Neon account.

## 9. Interfaces & Contracts

- **Ingestion contract** — every adapter emits the same intake event: `{source_type,
  source_id, repo_ref, fetched_at, raw_payload, normalized_fields}`. Core never sees
  source-specific quirks. (invariant 4)
- **Markdown front-matter schema** — a **versioned public contract** (Hugo + future models
  consume it): `schema_version`, canonical `id`, `core`, `status`, `verification`,
  `sources[]`, `correlations[]`, `updated`. Field renames are breaking changes.
- **Views/repository seam** — all reads/writes go through a typed views layer; no business
  logic touches raw SQL. (invariant 5)

## 10. Security & Access

- **Access:** Cloudflare Access restricts the entire site to dev members (Google/GitHub/
  email identity). No public exposure → user content/usernames may be stored freely.
- **Secrets:** GitHub App private key, getmangos session, OpenRouter key, Postgres creds
  in Cloudflare secrets / container env — never in the git ledger.
- **Politeness budget:** a central token-bucket governs GitHub (5k/hr — already tripped
  once) and IPS (gentle delay, logged-in client). Never raw retry loops; back off + queue.

## 11. Edge Cases & Failure Modes

| Case | Handling |
|---|---|
| Tracker category says "active" but bug is `Completed`. | Resolution computed from code evidence, not URL/category. |
| `open_issues_count` counts PRs. | Use issue-vs-PR type split, never the counter. |
| Same bug across 4 forks (cross-core). | `report⟷report` dup edges; shown once as "affects Zero+Two+Three." |
| IPS field/comment behind login. | Authed crawl with dev-account session in KV; refresh on expiry. |
| GitHub rate limit (5k/hr). | App-token client + backoff + Queue cooldown. |
| PR force-push / tracker edit. | New immutable `source_record` version; never overwrite. |
| Hard "delete". | `retracted` event; reversible; nothing dropped. |
| Embedding model swap. | Re-embed from immutable raw; pinned dim avoids schema churn. |
| Drift needs real git (cross-fork compare). | Runs in the Python container, not Workers. |
| IPS has no webhooks. | Cron re-crawl; diff by last-comment date to avoid full re-scrape. |
| Future addon adds new IPS fields. | Raw JSONB ingest; map known fields on top; no migration to ingest. |
| Total rebuild needed. | Replay R2 raw + git ledger into a fresh DB → identical site. |

## 12. Phased Build Plan

Each phase is independently shippable. **MVP boundary = end of Phase 1.**

- **Phase 0 — Scaffold.** Verify Cloudflare capabilities (§13), provision Postgres,
  create `mai-data` repo + Hugo skeleton + Access, define schema + migrations, build the
  Registry adapter.
- **Phase 1 — Ingest + read-only mirror (MVP).** GitHub harvester + IPS crawler →
  canonical store → `.md` projection → dashboard showing per-core bug lists + global
  counts. No correlation yet; pure aggregation.
- **Phase 2 — Correlation & verification.** Bug↔PR/commit matching (explicit refs →
  subsystem → embeddings); surface "likely fixed by …" verdicts with confidence.
- **Phase 3 — Drift Observatory.** Cross-core divergence engine; sync matrix + per-core
  drift slices.
- **Phase 4 — Write-back (future, gated).** Optional, behind a WRITE_MODE-style trust gate:
  Mai posts breadcrumbs/status to GitHub/IPS; consume structured in-game-addon reports.

## 13. Open Questions & Risks

| # | Question / Risk | Owner |
|---|---|---|
| 1 | Confirm Cloudflare plan: Workers Paid, Queues, **Containers** availability, R2, Hyperdrive. | r-log |
| 2 | Neon (vs Supabase) account + region; does free tier cover MVP? | r-log |
| 3 | OpenRouter embeddings: exact model + endpoint (OpenAI-compatible?) + dimension. | r-log |
| 4 | IPS authed-crawl mechanics (session/cookie lifetime) + ToS comfort for mirroring. | r-log / Antz |
| 5 | Final tracked-repo list blessed by Antz (reconcile README vs his verbal list). | Antz |
| 6 | Drift **granularity**: file-level vs subsystem-level; definition of "behind by N". | r-log |
| 7 | Container vs $5 VPS for the Python/git worker if CF Containers is immature. | r-log |

## 14. Glossary

- **Core / fork** — a getMaNGOS server variant (Zero=Classic, One=TBC, Two=WotLK, Three=Cata…).
- **Drift / sync** — divergence between sibling forks in shared subsystems.
- **IPS** — Invision Community, the software behind getmangos.eu's bug-tracker.
- **Ledger** — the `mai-data` git repo of `.md`; durable, diffable projection of the DB.
- **Brain** — the Postgres+pgvector store; live operational truth.
- **Lens** — a view over the spine (Bug Hub or Drift Observatory).
- **Replayable** — rebuildable from immutable raw + ledger into an identical site.

## 15. References

- GITA (pipeline reference): https://github.com/r-log/GITA
- Repo registry: https://github.com/mangos/MaNGOS/blob/master/README.md
- getmangos.eu bug-tracker: https://www.getmangos.eu/bug-tracker/
- Spec structure: `_spec-template.md`
