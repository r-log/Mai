---
title: "Mai — Port-Debt Board (multi-user, live, self-fresh)"
status: Draft
version: 0.1
owners: [r-log]
related:
  - port-debt-board.md
  - sync-intelligence-engine.md
  - framework-architecture.md
  - dashboard-workspace-redesign.md
  - web-design.md
---

# Mai — Port-Debt Board (multi-user, live, self-fresh)

> The Sync Intelligence Engine produces a trustworthy port-debt backlog, and the static
> v1 board (`port-debt-board.md`) puts it on screen — but per-browser: state lives in one
> user's `localStorage`, the site is `localhost`, and the data only refreshes when someone
> runs the CLI by hand. This spec turns Mai into the **live, shared, self-updating
> cross-fork porting cockpit** Antz asked for: an always-on backend that (a) **stays
> truthful on its own** — a read-only GitHub App fires the refresh the moment a PR merges,
> with cron as the always-on backstop — and (b) holds **shared board state** behind a
> **closed login** (admin-provisioned accounts, no public sign-up) so the people we invite
> **claim or get assigned** ports, and everyone signed in sees the same board across
> all four cores. The hard guarantee underneath it: **the engine owns "what's true" (proven
> from code); humans own "what we're doing about it" (assignment + workflow). A human click
> can never make the site lie**, because "still needs porting" is always recomputed from the
> actual diffs, and a card auto-disappears the moment the fix really lands.

<!-- Follows the docs/specs/ numbered-section convention. Terse; tables/bullets over prose. -->

## 1. Summary

This spec makes the port-debt board **live and collaborative** by standing up one always-on
backend that the static site so far could not have. The backend does three jobs: it runs the
existing engine's **refresh cycle** on a schedule + GitHub-App webhook so the data is fresh
without anyone touching a CLI; it serves a small **board API behind a login** (admin-provisioned
username + password; claim / assign / set-status / dismiss a port) backed by a new **`BoardItem`**
human-intent layer keyed to the engine's `PortCandidate`; and it hosts the git-worker that already
produces the truth. The site moves off `localhost` to a real URL; the `/port/` page keeps its **target-fork columns**
but gains **view toggles** (All cores · My ports · By person), live assignee/status from the API,
and self-claim + maintainer-assign. Audience: getMaNGOS maintainers (Antz, MadMax) + r-log. It
replaces the v1 `localStorage` triage (the seam v1 reserved) with shared server state — and turns
"what does each fork still need, and who's on it?" into one always-current screen.

## 2. Goals & Non-Goals

**Goals**
- **Self-freshness:** a single `run_refresh_cycle()` driven by a **GitHub App webhook** (instant on
  push / PR-merge) with a **cron backstop** (always on), so the site is truthful with no manual step.
- **Closed identity:** a **username + password login**; accounts are **admin-provisioned** (we run a CLI
  to create each one and hand out credentials privately) — **no public sign-up, no anonymous access**.
  The login is the **sole gate**: no valid session → nothing but the login page.
- **First-login password change:** the password we issue is one-time; on first login the user must set
  their own (a `must_change_password` flag), so the DM'd credential never stays the real one.
- **Shared board state:** a server-side **`BoardItem`** per port candidate — assignee + workflow
  status + dismissal — visible to everyone, with an append-only history of who-did-what-when.
- **Assignment:** anyone logged in can **self-claim**; **maintainers** (accounts flagged `is_maintainer`,
  e.g. Antz, r-log) can **assign / reassign** others. One assignee per card.
- **All cores at a glance:** keep target-fork columns; add **All cores · My ports · By person** views
  and tier/subsystem/source filters over the live data.
- **Hosting:** deploy the backend + git-worker on one small always-on box (VPS / Fly.io), board &
  derived data in **Neon Postgres**, the static Hugo site on **Cloudflare Pages**.
- **Keep the truthfulness guarantee:** engine owns existence (recomputed from code, auto-resolves on
  port); humans own intent. A human action never marks a fix "ported."

**Non-Goals**
- **No write-back to GitHub/IPS.** The App is read-only (`*:read`); Mai writes nothing external.
- **No automatic porting.** Mai assigns, tracks, and links PRs; humans port.
- **No general project-management surface** (sprints, time-tracking, comments threads). Just the
  porting cockpit: who's taking which fix, what stage it's at.
- **No new engine truth logic.** Propagation / classification / PortCandidate are consumed as-is; this
  spec adds the freshness *trigger*, the *delivery* (hosting), and the *human-intent* layer.
- **No multi-tenant / per-user private boards in v1.** One shared board for the invited group.
  (Personal lenses are *filters* over the shared board, not separate boards.)
- **No public registration / self-signup.** There is no sign-up page; accounts exist only because an
  admin created them. **Login only.** (Account creation is a CLI run by us on the box.)
- **No GitHub OAuth / third-party identity.** Identity is our own account table, nothing external.

## 3. Context & Constraints

- **What exists (verified 2026-06-21):**
  - Engine is complete through `PortCandidate` (fields: `patch_group_id, source_core, target_core,
    subsystem, classification, magnitude, tier, confidence, evidence, status ∈ {open,ported,dismissed},
    source_sha`). `sync-analyze` runs propagation → classification → port-candidates.
  - The pipeline is **entirely manual**: CLI subcommands (`registry-load, harvest, ips-crawl, enrich,
    embed, correlate, drift, commits-harvest, sync-analyze, publish`) then `hugo`. **There is no
    trigger / webhook / scheduler in the codebase** (grepped: no `Trigger`, no `run_refresh_cycle`,
    no webhook receiver). Freshness is 100% manual today.
  - The v1 board (`port-debt-board.md`, merged) is static Hugo + `port_candidates.json` + `portboard.js`
    with a **`localStorage` triage** (`working/done/dismissed`) — single-user, per-browser, not deployed.
- **Sync-engine spec already designed the trigger** (`sync-intelligence-engine.md` §7 refresh cycle,
  §9 `Trigger` protocol, Phase 4). This spec **implements that Phase-4 freshness** and adds the board.
- **Ownership reality:** r-log does not admin the getMaNGOS repos. The GitHub App is installed on
  r-log's **own** forks (`r-log/server`, `r-log/server-two`, …) first via smee.io, then Antz installs
  the identical App on getMaNGOS — **no code path hard-codes a fork** (registry-driven; sync-engine
  Invariant 8).
- **Stack constraints:** Python 3.12 + async SQLAlchemy 2.0 + httpx + pydantic-settings + pytest, with
  `Fake*` protocol seams and a repository seam (SQLite local → Neon deploy); 4-space indent; `feat:`-style
  commits; **no AI attribution**; read-only externally; `.env` holds live secrets, never committed.

## 4. Invariants (Non-Negotiable Rules)

1. **Engine owns truth; humans own intent — and intent can never fake truth.** A candidate exists iff
   the engine computes it absent-in-target from real diffs. No board action sets a fix "ported"; only the
   engine, by detecting the patch-id in the target's code, does — and then the card auto-leaves.
2. **Read-only externally.** App scopes are `*:read`; Mai writes nothing to GitHub/IPS. The only writes
   are to Mai's own DB (board state).
3. **Trigger-agnostic, self-reconciling.** One `run_refresh_cycle()`; webhook accelerates it, cron always
   backstops it, so a dropped webhook can never leave the site stale (sync-engine Invariant 6).
4. **Raw append-only; derived recomputable; board state durable & audited.** `Commit*` raw is immutable;
   `PortCandidate` et al. are rebuildable; `BoardItem` is durable human state with an **append-only
   `BoardEvent` history** (never silently overwritten).
5. **Stable join key.** Board state keys on `port_candidate` identity (`patch_group_id + target_core`),
   the same id v1 used — so a re-derived candidate keeps its assignee/status across refreshes.
6. **Auto-resolve archives, never deletes.** When the engine marks a candidate `ported`, its `BoardItem`
   is **archived** (kept for history/credit), not dropped — "who ported what" stays answerable.
7. **Closed identity; login is the gate.** Every page and API call requires a valid session from an
   **admin-provisioned account**; there is no self-registration and no anonymous read or write. Every
   assignment/action is attributed to its username.
8. **Passwords are never stored in clear.** Stored as an **argon2id** hash; a freshly-provisioned account
   carries `must_change_password=true` and must set its own password before doing anything else.
9. **Maintainer is data, not code.** Whether an account may assign-others / dismiss / create accounts is the
   `is_maintainer` flag on its `User` row, set at creation — never hardcoded.
10. **Install-target-agnostic.** Tracked forks come from the registry; the webhook carries the installation
    id; nothing assumes r-log's forks vs getMaNGOS's.

## 5. System Architecture

```
  Forks on GitHub (zero/one/two/three)
      │ App webhook: push / pull_request          │ cron (backstop, always on)
      ▼                                            ▼
  ┌──────────────────────────────────────────────────────────────────────┐
  │  Mai backend  (one always-on box: VPS / Fly.io)                        │
  │                                                                        │
  │  Trigger seam ── WebhookTrigger (HMAC-verified) ─┐                     │
  │                  CronTrigger (interval) ─────────┤                     │
  │                                                  ▼                     │
  │  run_refresh_cycle():  git fetch mirrors → commits-harvest →           │
  │     harvest PRs → sync-analyze → publish JSON → trigger Pages build    │  ENGINE (exists)
  │                                                                        │  + FRESHNESS (new)
  │  Board app (FastAPI):  /login · /set-password · GET board ·           │  BOARD (new)
  │     POST claim/assign/status/dismiss   (session required for ALL)      │
  │     auth: username + password (admin-provisioned, argon2id) → session  │
  │     reads PortCandidate (truth) + writes BoardItem/BoardEvent (intent) │
  └──────────────────────────────────────────────────────────────────────┘
      │ git-worker mirrors (disk)        │ SQL                 │ serves gated UI + JSON
      ▼                                  ▼                     ▼
  mirrors/<core>.git (volume)     Neon Postgres          Hugo-built assets served
                                  (derived + board + users)  BEHIND the login session
                                                                  │  fetch board state
                                                                  ▼
                                          /port/  → live shared board (cols + toggles + assignees)
                                          (no valid session → only /login is reachable)
```

- **Truth topology:** raw `Commit*` + git mirrors = code truth; `PortCandidate` = derived truth;
  `BoardItem` = durable human intent. Three layers, one join key (§Invariant 5).
- **Delivery topology:** because the login is the **sole gate**, the board is **not** a publicly-readable
  static site. The backend serves the login page and, only to an authenticated session, the `/port/` page
  + its `port_candidates.json` snapshot (the card spine) and the live `BoardItem` overlay. Hugo still
  *builds* the static assets; the backend *serves* them behind the session (a static mount), rather than a
  public Cloudflare Pages site. There is no anonymous read-only view.
- **The git-worker is the one stateful component** (needs a disk for bare clones) — hence a box with a
  volume, not a pure edge function (sync-engine §8).

## 6. Data Model

### 6.1 Engine truth (exists — unchanged)
`PortCandidate(patch_group_id, source_core, target_core, subsystem, classification, magnitude, tier,
confidence, evidence, status, source_sha)`. **Refinement:** for the board, engine `status` is treated as
`{open, ported}` only — `ported` is engine-set on auto-resolve. **Human "dismiss" no longer mutates the
engine row** (v1 wrote it to `localStorage`); it becomes `BoardItem` state (§6.2), so the engine stays a
pure code-truth function and dismissal is shared + reversible + audited.

### 6.2 Board state (new — durable human intent)

| Entity | Key | Fields |
|---|---|---|
| **BoardItem** | `port_candidate_id` = `f"{patch_group_id}:{target_core}"` | `assignee` (username \| null), `status` ∈ {open, claimed, in_progress, pr_linked, dismissed}, `related_pr` (url \| null), `dismiss_reason` (null unless dismissed), `archived` (bool — set when engine auto-resolves), `updated_by`, `updated_at` |
| **BoardEvent** | `(board_item, seq)` | append-only audit: `actor` (username), `action` ∈ {claim, assign, unassign, status, link_pr, dismiss, restore, auto_resolve}, `from`, `to`, `at` |
| **User** | `username` | `password_hash` (argon2id), `display_name`, `is_maintainer` (set at creation), `must_change_password` (bool; true on a freshly-provisioned account), `created_at`, `last_login` |

- **`status` is workflow, not truth.** `pr_linked` means "a PR is open," **not** "ported." Only the engine
  flips a candidate to `ported`; that auto-archives the `BoardItem` (§Invariant 6) and the card leaves the
  board — regardless of its human status.
- **`dismissed`** = a human decision "won't port / N/A" (requires `dismiss_reason`); hides the card under
  default filters, reversible (`restore`), fully audited. The engine still considers the code absent; if it
  later detects the fix landed, it auto-resolves anyway.
- **No `BoardItem` is auto-created**; a row is materialized on first human action (claim/assign/dismiss).
  Un-actioned candidates render as plain `open` cards from the JSON snapshot.

## 7. Pipeline & Data Flow

**Refresh (truth) — one cycle, two triggers** (implements sync-engine §7, Phase 4):
1. `WebhookTrigger` (App `push`/`pull_request`, HMAC-verified, debounced) or `CronTrigger` (interval)
   calls **`run_refresh_cycle()`**.
2. The cycle runs the existing stages incrementally (cursor-gated, idempotent): `git fetch` mirrors →
   `commits-harvest` → `harvest` PRs → `sync-analyze` → `publish` writes `port_candidates.json` & friends.
3. **Reconcile board:** for every candidate now `ported`, archive its `BoardItem` (+`auto_resolve` event).
   New candidates simply appear in the snapshot; existing `BoardItem`s re-bind by stable id (§Invariant 5).
4. Trigger a **Cloudflare Pages** rebuild of the static site from the fresh JSON.

**Auth (gate) — API:**
- `POST /login` (username + password) → on success issues a session cookie; if `must_change_password`,
  the session is restricted to `/set-password` until the user sets a new one. `POST /logout` clears it.
- `POST /set-password` (authenticated) → verifies the new password, argon2id-hashes it, clears
  `must_change_password`. **Every other route requires a valid (unrestricted) session.**

**Board mutation (intent) — API:**
- `GET /api/board` → merges the current `port_candidates.json` snapshot with `BoardItem` rows → the live
  board (cards + assignee/status). **Requires a valid session** (login is the gate).
- `POST /api/board/{id}/claim` (any logged-in user) · `/assign` (maintainer; body = username) · `/status` ·
  `/link-pr` · `/dismiss` (reason) · `/restore`. Each validates session + role, upserts `BoardItem`, appends a
  `BoardEvent`. Idempotent where natural (re-claiming by the same user is a no-op).

## 8. UX Design — `/port/` (live)

- **Login:** a username + password form is the only thing an unauthenticated visitor can reach — there is
  no public/read-only view. First login with a freshly-issued password redirects to **Set your password**
  before the board is shown. After login: claim/status; maintainers also = assign/dismiss; a logout control.
- **Spine:** columns **Port into ZERO / ONE / TWO / THREE**, each tier-sorted (surgical→bulk), every fork
  shown even if empty ("all cores", never "broken").
- **View toggles:** **All cores** (default) · **My ports** (cards assigned to me) · **By person** (group by
  assignee). Filters: tier · subsystem · source fork · text search. Toggles/filters are client lenses over
  one dataset.
- **Card:** tier dot · `from <source_core>` · title · `subsystem · magnitude lines` · **assignee chip**
  (avatar/login or `[ Claim ]`) · **status pill** (`open → claimed → in progress → PR #123`) · 🔗 source
  commit · expand for evidence + history. Maintainers see an `Assign ▾` and `Dismiss` affordance.
- **Freshness indicator:** "updated <time> ago" from the last refresh; a subtle marker when the App webhook
  (vs cron) drove it.
- **Fail-soft:** if the backend is up but a *board mutation* fails (e.g. someone else just claimed a card),
  the UI shows the current state and a clear notice — it never silently drops the action. (There is no
  anonymous static fallback: the board is only reachable with a session.)

## 9. Interfaces & Contracts

- **GitHub App** — permissions `metadata:read, contents:read, pull_requests:read, issues:read`; events
  `push, pull_request`. No write scopes. (From sync-engine §9; this spec installs/operates it.)
- **`Trigger` protocol** (new) — `WebhookTrigger`, `CronTrigger`; both call `run_refresh_cycle()`. `FakeTrigger`
  for tests. Webhook handler verifies HMAC, debounces, maps installation id → registry forks.
- **Auth** (new) — `PasswordHasher` seam (argon2id via `argon2-cffi`, with a `FakeHasher` for fast tests);
  session middleware that rejects any request without a valid (unrestricted) session; `mai user-add
  <username> [--maintainer]` CLI that creates a `User` with a generated one-time password (printed once)
  and `must_change_password=true`; plus `mai user-list`. **No registration endpoint exists.**
- **Board API** (new, FastAPI) — endpoints in §7; request/response pydantic schemas; auth via a signed
  session cookie issued on username+password login; role checks against the `is_maintainer` flag.
- **Repositories** (new, behind the seam) — `BoardItemRepository`, `BoardEventRepository`, `UserRepository`.
- **`GET /api/board` contract** — `{ summary, columns:[{core, count, candidates:[{...port fields..., board:{
  assignee, status, related_pr, dismissed, history_count}}]}] }`; the engine fields mirror
  `port_candidates.json` exactly; `board` is the merged overlay (null when no `BoardItem`).
- **Static `port_candidates.json`** — unchanged producer; remains the read-only spine + offline fallback.

## 10. Security & Access

- **Auth:** username + password against an **admin-provisioned** `User` table. Passwords stored as
  **argon2id** hashes (never plaintext, never reversible); login issues a signed, HTTP-only, `Secure`,
  `SameSite` session cookie. CSRF protection on all mutating routes. Generic "invalid username or password"
  on failure (no user-enumeration); rate-limit/backoff on repeated failures.
- **Login is the sole gate:** session middleware rejects every unauthenticated request (302 → `/login`);
  there is **no anonymous read path** to the board or its data. No third-party identity, no OAuth.
- **First-login change:** a provisioned account's `must_change_password=true` confines its session to
  `/set-password` until it sets its own password — so a credential shared over DM is never the standing one.
- **Authorization:** any logged-in user may claim/status their own work; **assign-others, dismiss, restore,
  and account creation** require `is_maintainer`, enforced server-side (never trust the client).
- **Secrets:** session-signing key, the (data) GitHub App id/private key/webhook secret in `.env`
  (gitignored) / platform secret store; webhook deliveries HMAC-verified. Public-repo data only; no PII
  beyond public commit/author metadata.
- **Read-only externally** remains absolute — the data App can never write to GitHub; Mai's only writes are
  to its own DB.

## 11. Edge Cases & Failure Modes

| # | Case | Handling |
|---|------|----------|
| 1 | Human marks `pr_linked`/`in_progress` but code never ports | Card stays on the board; status is workflow only. Truth is unchanged — the engine still sees it absent. |
| 2 | Fix actually lands in target | Engine flips candidate `ported` on next refresh → `BoardItem` archived (+`auto_resolve` event), card leaves. Works even if no human ever touched it. |
| 3 | Dropped/duplicate webhook | Cron backstop reconciles regardless; refresh is idempotent so a duplicate is a no-op. |
| 4 | Candidate re-derived under a new patch group (rare) | Stable id is `patch_group_id:target_core`; if the group id changes, the old `BoardItem` archives (no current candidate) and a fresh one starts — audited, never silently moved. |
| 5 | Two users claim the same card near-simultaneously | First write wins (unique `BoardItem` upsert + optimistic check); second sees the current assignee and a "already claimed by @x" notice. |
| 6 | Non-maintainer tries to assign others / dismiss / create account | 403 server-side; UI hides the affordance but the server is the gate. |
| 7 | Dismissed candidate still absent in code | Stays dismissed (with reason) under default filters; `restore` brings it back; engine auto-resolve still overrides if it ports. |
| 8 | Backend/app down | The whole board is behind the session, so it is simply unavailable (login page errors) — there is **no** anonymous static fallback by design. Restart-on-failure (runbook) keeps downtime short. |
| 8a | Wrong password / unknown username | Generic "invalid username or password" (no enumeration); failed attempts rate-limited/backed-off. |
| 8b | User with `must_change_password` tries to reach the board | Session is confined to `/set-password`; any other route 302s back to it until the password is set. |
| 8c | Lost/forgotten password | A maintainer re-runs `mai user-add`-style reset (re-issues a one-time password, sets `must_change_password=true`); no self-service email reset in v1. |
| 9 | Force-push / rebased fork history | Cursor stored as SHA; re-walk from merge-base (sync-engine §11.9); raw commits append-only, so board ids stay stable. |
| 10 | First clone of four full repos is large/slow | One-time per fork on the worker volume; thereafter incremental `git fetch` (sync-engine §11.7). |
| 11 | Webhook storm (many pushes) | Debounce/coalesce into one cycle (sync-engine §10). |

## 12. Phased Build Plan

Each phase ships something usable and leaves the prior state working.

- **Phase A — Deploy + cron freshness + live read-only board.** Stand up the backend box (FastAPI +
  git-worker + cron) + Neon + Cloudflare Pages; implement `Trigger` seam with **`CronTrigger`** +
  `run_refresh_cycle()` (orchestrating the existing stages incrementally); deploy the static site to a real
  URL behind an interim Cloudflare Access gate (**replaced by the app login in Phase B**); Pages rebuild on
  refresh. **Outcome: an always-on, self-refreshing, all-cores port-debt site you can show Antz** (board
  still read-only). *This is the fastest value drop.* *(Phase A is built + merged.)*
- **Phase B — Closed login + shared board state.** `User` model + argon2id `PasswordHasher` seam + `mai
  user-add`/`user-list` CLI (admin-provisioned accounts, one-time password, `must_change_password`); session
  middleware as the **sole gate** (`/login`, `/logout`, `/set-password`, no registration); `BoardItem/
  BoardEvent` models + repositories; board API (claim/assign/status/link-pr/dismiss/restore) with role
  enforcement; `/port/` served behind the session, hydrating live state and exposing **All cores · My ports ·
  By person** + assign/claim UI. **Outcome: the collaborative cockpit** — gated, shared, assignable, audited.
- **Phase C — Instant webhook + GitHub App.** Add **`WebhookTrigger`** (App `push`/`pull_request`, HMAC,
  debounce) + smee.io dev forwarding; install the read-only App on r-log forks; wire installation-id →
  registry. **Outcome: refresh is instant on PR-merge**, cron now backstop-only.
- **Phase D — Hand-off readiness.** Maintainer spot-audit of high-confidence candidates (sync-engine §12.6);
  docs for Antz to install the identical App on getMaNGOS; registry entries for production repos. **Outcome:
  install-ready for getMaNGOS.**

Phases A–B are the MVP Antz uses; C–D harden and generalize.

## 13. Open Questions & Risks

| # | Item | Owner |
|---|------|-------|
| 1 | Exact host (small VPS vs Fly.io machine) + volume sizing for four bare mirrors; pick one in Phase A. | r-log |
| 2 | Who gets `is_maintainer` at launch (r-log + Antz?); confirm the maintainer set when provisioning accounts. | r-log / Antz |
| 3 | Cron interval for the backstop (e.g. 1–3h) before the webhook lands in Phase C. | r-log |
| 4 | Session store: signed stateless cookie vs server-side session table (start signed-cookie; revisit if revocation needed). | r-log |
| 5 | Serving model shift: Phase A's public Cloudflare Pages deploy → Phase B serves `/port/` + JSON **behind the login session** (backend static mount). Confirm the Phase A Pages site is retired/locked once B lands so nothing is anonymously readable. | r-log |
| 5a | Login-failure rate-limit / lockout policy (simple per-IP backoff in v1?). | r-log |
| 6 | Neon free-tier limits for board state + derived data; Hyperdrive vs direct connection from the box. | r-log |
| 7 | Should "dismiss" ever feed back to the engine as a suppression signal, or stay purely board-side? (v1: board-side only.) | r-log |
| 8 | r-log forks may lag real getMaNGOS activity; supplement with periodic real-repo audits (sync-engine §14.6). | r-log / Antz |

## 14. Glossary

- **Engine truth** — "does this fix exist in the target fork?", computed from `git patch-id` propagation; the
  only thing that can mark a candidate `ported`.
- **Human intent** — assignment + workflow status + dismissal; lives in `BoardItem`, never alters truth.
- **BoardItem** — durable shared human state for one `PortCandidate` (assignee, status, related_pr, dismissed).
- **BoardEvent** — append-only audit entry (who did what, when) for a `BoardItem`.
- **run_refresh_cycle()** — the one idempotent function that brings the engine + site up to date; webhook
  accelerates, cron backstops.
- **Maintainer** — an account with `is_maintainer=true`, allowed to assign-others / dismiss / restore / create accounts.
- **Admin-provisioned account** — a `User` created by a maintainer via `mai user-add`; there is no self-registration.
- **Sole gate** — the login: every page/API requires a valid session; no anonymous read or write exists.
- **Target / source fork** — the fork that lacks / has the fix (board column / card `from`).
- **Tier** — magnitude band: surgical ≤50 / small ≤500 / moderate ≤5000 / bulk >5000 (portable lines).

## 15. References

- Builds on: `port-debt-board.md` (static v1; this replaces its `localStorage` seam with shared state),
  `sync-intelligence-engine.md` (§7 refresh cycle, §9 `Trigger`/App, Phase 4 freshness — implemented here),
  `framework-architecture.md` (seams/invariants), `dashboard-workspace-redesign.md` + `web-design.md`
  (site/board UI patterns).
- Real-data validation: `mai/mai-data/tmp/real_run.py` (r-log forks; 242 open candidates as of 2026-06-17).
- Operational hand-off target: the identical read-only GitHub App, installed by Antz on getMaNGOS.
