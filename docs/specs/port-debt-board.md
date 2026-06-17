---
title: "Mai — Port-Debt Board (static personal v1)"
status: Draft
version: 0.1
owners: [r-log]
related:
  - sync-intelligence-engine.md
  - dashboard-workspace-redesign.md
  - web-design.md
  - framework-architecture.md
---

# Mai — Port-Debt Board (static personal v1)

> The Sync Intelligence Engine now produces a trustworthy, tier-sized port-debt backlog
> (`PortCandidate` rows: a fix present in one fork, absent in another, in a shared subsystem,
> with confidence + evidence). This spec puts that backlog **on screen** as a usable board:
> a new static-Hugo `/port/` page, columns by **target fork** ("port into X"), cards sorted
> **quick-wins-first**, each with a human title, a link to the source commit, and an expandable
> evidence trail. A **personal triage overlay** (working / done / dismissed) lives in
> `localStorage`. No backend — this ships fast on the existing stack and delivers the original
> "make Mai valuable" goal. Multi-user (shared boards, auth) is a deliberate later spec; the
> `localStorage` map is the seam it will replace.

<!-- Follows the docs/specs/ numbered-section convention. Terse; tables/bullets over prose. -->

## 1. Summary

A read-only, static, single-user board that renders the engine's open `PortCandidate` rows.
The pipeline gains one export (`build_port_candidates` → `port_candidates.json`); the site gains
one page (`/port/`) and one script (`portboard.js`). The board groups candidates into per-target-fork
columns ("Port into zero/one/two/three"), sorts each column surgical→bulk then by magnitude, shows a
card per fix (title, source fork, subsystem, tier, magnitude, link, expandable evidence), and lets the
user tag each card `working`/`done`/`dismissed` in `localStorage`. Audience: getMaNGOS maintainers
(Antz, MadMax) + r-log. It answers, per fork, *"what fixes from my siblings still need to come in?"*

## 2. Goals & Non-Goals

**Goals**
- Expose open `PortCandidate` data to the site as **`port_candidates.json`** via a new `dataviz.py`
  builder (only `status == "open"`; grouped by target fork; sorted quick-wins-first).
- A new **`/port/` page**: per-target-fork columns, tier-badged cards, in-card **evidence** expander,
  a clickable **source-commit link**, and a human **title** (the source commit subject).
- **Filters**: by tier, by source fork, and a text search.
- A **personal triage overlay** in `localStorage` (`working`/`done`/`dismissed`), stable across re-runs,
  self-pruning when a candidate leaves the export.
- Stay **static-Hugo-buildable & offline**; write nothing to GitHub or the engine DB.
- Leave the existing dashboard porting board (`board.js`/`pushes.json`) untouched.

**Non-Goals**
- **No backend, no auth, no shared/multi-user state** — that is a separate future spec
  (`port-debt-board-multiuser.md`). This board is per-browser.
- **No write-back** to the engine DB or GitHub. Triage is client-local only.
- **No new engine logic** — consumes `PortCandidate` as-is (built by the merged Sync Engine).
- **No JS framework** — vanilla + the existing Hugo/Primer styling, matching `board.js`.
- Not replacing the dashboard's "what recently landed" board; the two answer different questions.

## 3. Context & Constraints

- **Builds on** the merged Sync Intelligence Engine (`PortCandidate` has `patch_group_id, source_core,
  target_core, subsystem, classification, magnitude, tier, confidence, evidence, status, source_sha`).
  Verified: nothing in `src/mai/publish/` references `PortCandidate` yet — the export is net-new.
- **Existing site pattern** (Plan 08 + `dashboard-workspace-redesign.md`): `publish/dataviz.py` builds
  `data/*.json`; Hugo embeds it via `{{ .Site.Data.X | jsonify | safeJS }}`; vanilla JS (`board.js`,
  `frequency3d.js`) renders it client-side and persists personal state to `localStorage`. This spec
  follows that pattern exactly. The double-encode bug (Hugo `<script>`-context escaping) is avoided
  with `jsonify | safeJS`.
- **Real-data validation (2026-06-17 run, r-log forks):** 242 open candidates, tiers
  surgical 157 / small 62 / moderate 20 / bulk 3, across all four forks as targets — the board has real,
  tier-sized content to render today.
- **Constraints:** Python 3.12 + async SQLAlchemy (server); vanilla JS + Hugo (client); read-only
  externally; 4-space indent; `feat:`-style commits; **no AI attribution**.

## 4. Invariants (Non-Negotiable Rules)

1. **Read-only externally.** The board writes nothing to GitHub or the engine DB. Personal triage is
   `localStorage` only.
2. **Static & offline-first.** `/port/` builds with `hugo` and renders without a backend. Degrades to a
   readable empty-state if `port_candidates.json` is missing/empty or JS/`localStorage` is unavailable.
3. **Engine owns truth; client owns intent.** A candidate appears iff the engine exported it (`open`).
   The client never decides a fix is ported — it only records the human's `working`/`done`/`dismissed`
   intent over what the engine shows.
4. **Stable ids, sticky + self-cleaning.** Card id = `patch_group_id:target_core`. Triage persists across
   re-runs by id; on load, ids absent from the current export are pruned from `localStorage`.
5. **Data, not duplication.** The page reads generated JSON; no hardcoded candidates. One source of
   truth (engine DB → `build_port_candidates` → JSON).
6. **Progressive enhancement.** Columns, cards, titles, and links are readable as plain HTML/JSON with no
   JS; filtering, evidence-expand, drag/triage layer on top.
7. **localStorage is the multi-user seam.** The triage map's id/state shape is exactly what a future
   per-user backend will own — no shape that blocks that swap.

## 5. System Architecture

```
 DB: PortCandidate (status=open) + Commit (for title) + Repo (for source_url)
        │  publish/dataviz.py → build_port_candidates(session)  [NEW]
        │                       write_dataviz writes data/port_candidates.json  [NEW line]
        ▼
 mai-data/
   data/port_candidates.json          ← NEW export (summary + per-target-fork columns)
   layouts/port/list.html             ← NEW page: embeds window.MAI_PORT (jsonify|safeJS), columns shell
   static/js/portboard.js             ← NEW: render columns/cards, filters, evidence-expand, triage
   static/css/mai.css                 ← extended: port-board components (reuse existing tokens)
        │  hugo build
        ▼
 /port/  → 4 target-fork columns of tier-sorted cards
 localStorage["mai.portdebt"] = { "<id>": "working|done|dismissed", "_v": 1 }   ← personal overlay
```

- **Data topology:** the engine DB is the source of truth; `port_candidates.json` is a derived view;
  `localStorage` is the only writable (personal) state.
- **Presentation topology:** Hugo renders the page shell + embeds the JSON; `portboard.js` does the
  dynamic render/filter/triage. Fail-soft throughout.
- **Server change is additive:** one builder + one write line in `dataviz.py`. No engine change.

## 6. Data Model — `port_candidates.json`

```json
{
  "summary": { "total": 242, "tiers": {"surgical":157,"small":62,"moderate":20,"bulk":3} },
  "columns": [
    {
      "core": "three",
      "repo": "r-log/server",
      "count": 130,
      "candidates": [
        {
          "id": "0b1c…:three",
          "title": "Fix realm auth packet length",
          "source_core": "two",
          "source_url": "https://github.com/r-log/server-two/commit/606c7b55a03e…",
          "subsystem": "src/shared/Auth",
          "tier": "surgical",
          "magnitude": 12,
          "confidence": "high",
          "patch_id": "e8d14f9d108c",
          "evidence": ["present in two@606c…", "shared subsystem src/shared/Auth", "absent in three"]
        }
      ]
    }
  ]
}
```

- **`id`** = `f"{patch_group_id}:{target_core}"` (stable localStorage key).
- **`title`** = first line of the source commit's `message`, joined via `Commit(core=source_core,
  sha=source_sha)`. Falls back to `f"{subsystem} fix ({patch_id[:8]})"` if the commit/message is missing.
- **`source_url`** = `f"https://github.com/{source_repo_full_name}/commit/{source_sha}"`, where
  `source_repo_full_name` comes from the `Repo` registry for `source_core` (the `*/server` repo). If no
  registry row, `source_url` is `null` (card shows no link).
- **Columns**: one per distinct `target_core` present in open candidates, ordered `zero, one, two, three`
  (known cores first, then any extra alphabetically). **Empty target forks still get a column** (empty
  state), so the board reads as "all forks" not "broken."
- **Sort within a column**: tier rank (`surgical<small<moderate<bulk`) then `magnitude` ascending.
- Only `status == "open"` candidates are exported. `ported`/`dismissed` (engine-side) are omitted.

## 7. Pipeline & Data Flow

Extends `publish/dataviz.py` + `publish` CLI (already wired):
1. **`build_port_candidates(session)`** — query open `PortCandidate`; for each, join `Commit` (title) and
   read the `Repo` registry (source_url); group by `target_core`; sort; assemble the §6 dict.
2. **`write_dataviz`** — add `(data / "port_candidates.json").write_text(json.dumps(...))` alongside the
   existing exports.
3. **Hugo build** — `layouts/port/list.html` embeds `window.MAI_PORT = {{ .Site.Data.port_candidates |
   jsonify | safeJS }}`; `portboard.js` reads it and renders.
   Deterministic + re-runnable like every Mai stage.

## 8. Page Design — `/port/`

Top to bottom:
1. **Header** — title "Port Debt" + the `summary` tier chips (surgical/small/moderate/bulk counts) + a
   freshness note.
2. **Filter bar** — tier select, source-fork select, text search (matches title/subsystem).
3. **Four columns** — "Port into ZERO / ONE / TWO / THREE", each with a count and its sorted cards.

**Card anatomy:** a tier dot + `from <source_core>`; the **title**; a meta line `subsystem · magnitude
lines`; a triage action row `[working] [done] [✕]`; a 🔗 to `source_url`. Click the card body to expand
the **evidence** list. Triage state restyles the card: `working` = accent border + "working" group;
`done` = strike-through + collapsed; `dismissed` = hidden unless "show dismissed" is on.

## 9. Interfaces & Contracts

- **`build_port_candidates(session) -> dict`** — the §6 schema; pure read; deterministic ordering.
- **`port_candidates.json`** — the §6 file; `portboard.js` validates shape and shows empty-state on
  mismatch.
- **`window.MAI_PORT`** — injected object (not string) via `jsonify | safeJS`.
- **`localStorage["mai.portdebt"]`** — `{ "<id>": "working|done|dismissed", "_v": 1 }`; the board's only
  writable state; pruned on load to ids present in `MAI_PORT`.
- **CSS** — new `.port-*` classes in `mai.css`; no inline colors except tier dots (data-driven).

## 10. Security & Access — N/A (internal, read-only)

Static site behind the existing Cloudflare Access (dev-only), same as the rest of Mai. No new secrets, no
PII beyond public commit metadata already in the engine. The board makes no network calls except loading
its own static JSON.

## 11. Edge Cases & Failure Modes

| # | Case | Handling |
|---|------|----------|
| 1 | `port_candidates.json` missing/empty | Page renders header + four empty-state columns; no JS error. |
| 2 | `MAI_PORT` double-encoded (Hugo escaping) | Use `jsonify \| safeJS`; `portboard.js` guards `typeof !== "object"` → fallback. |
| 3 | Source commit/message missing (title join fails) | `title` falls back to `"{subsystem} fix ({patch_id[:8]})"`. |
| 4 | Source repo not in registry (no `source_url`) | `source_url: null`; card omits the 🔗. |
| 5 | Candidate leaves export (engine auto-resolved/ported) | Not rendered; its `localStorage` entry pruned on next load. |
| 6 | Dismissed candidate reappears (still absent) | Stays dismissed (stable id); hidden unless "show dismissed". |
| 7 | `localStorage` unavailable/full | Triage degrades to in-memory for the session; board still renders. |
| 8 | A target fork has zero open candidates | Column shows an empty state ("nothing to port in"). |
| 9 | Huge column (100+ cards) | Tier-sorted; optional client-side "show more" cap per column (default show all, surgical first). |

## 12. Phased Build Plan

- **Phase 1 — Export.** `build_port_candidates` (+ `Commit`/`Repo` joins, grouping, sort, fallbacks) and
  the `write_dataviz` line; pytest for shape/sort/filter/joins. → `port_candidates.json` generated.
- **Phase 2 — Page shell + render.** `layouts/port/list.html` (embed `MAI_PORT`, columns shell, nav
  link), `portboard.js` render of columns/cards (title, tier dot, meta, source link), `mai.css` port
  components. Static, no triage yet. → `/port/` shows real columns on real data.
- **Phase 3 — Filters + evidence + triage.** Filter bar (tier/source/search), card evidence-expand, the
  `localStorage` triage overlay (working/done/dismissed) with prune-on-load. → the board is usable.
- **Phase 4 — Polish & verify.** Empty-states, degradation, full pytest green, `mai publish` + `hugo`
  build, re-run the real-fork harness and eyeball `/port/`. → ship.

Each phase yields a viewable site.

## 13. Open Questions & Risks

| # | Item | Owner |
|---|------|-------|
| 1 | `portboard.js` is not unit-tested (consistent with `board.js`/`frequency3d.js`) — rely on build + manual check; note the gap. | r-log |
| 2 | `source_url` assumes `Repo.full_name` is a GitHub `owner/repo`; the local `file://` test forks make a wrong link in test runs only — production (GitHub App) is correct. | r-log |
| 3 | Very large columns ergonomics; revisit a per-column cap/virtualization if real backlogs are big. | r-log |
| 4 | Whether to retire the dashboard `pushes.json` board once `/port/` exists; keep both for now. | r-log / Antz |
| 5 | Title quality depends on commit-message subjects; bulk/sync commits have vague subjects (acceptable; tier/subsystem still inform). | r-log |

## 14. Glossary

- **Port-debt** — a fix present in one fork, absent in a sibling, in a shared subsystem (engine `PortCandidate`).
- **Target fork** — the fork that *lacks* the fix; the board's column.
- **Source fork** — the fork that *has* the fix; where you port *from* (the card's `from`).
- **Tier** — magnitude band: surgical ≤50 / small ≤500 / moderate ≤5000 / bulk >5000 (portable lines).
- **Triage overlay** — the per-browser `localStorage` map of `working`/`done`/`dismissed` intent.

## 15. References

- Builds on: `sync-intelligence-engine.md` (the `PortCandidate` producer), `dashboard-workspace-redesign.md`
  (the static board + `localStorage` + `jsonify|safeJS` pattern), `web-design.md` (Primer tokens),
  Plan 08 (`publish/{dataviz,site}.py`).
- Real-data validation: `mai/mai-data/tmp/real_run.py` (r-log forks; 242 open candidates).
- Future consumer/successor: `port-debt-board-multiuser.md` (TBD) — auth + shared/private boards +
  assignment; replaces the `localStorage` seam (§Invariant 7).
