---
title: "Mai — Dashboard Workspace Redesign (web v2)"
status: Draft
version: 0.1
owners: [r-log]
related:
  - web-design.md
  - framework-architecture.md
  - 08-publish-hugo.md
---

# Mai — Dashboard Workspace Redesign (web v2)

> Turn the Mai landing page from a *report* into a *workspace*. The dashboard becomes
> a balanced overview whose hero is a **fully-playable 3D drift view** (replacing the
> flat heatmap there), followed by a **cross-core porting board** — per-core columns of
> "what landed" that a maintainer can **drag into a personal porting TODO**. All still
> static Hugo; the interactivity is client-side JS over JSON the pipeline generates.

## 1. Summary

web-design v1 (`web-design.md`, Built) delivered the GitHub/Primer chrome, area pills,
the flat drift heatmap, and a first 3D frequency-sheet hero on `/drift/`. This redesign
(v2) restructures the **landing dashboard** around the way Antz actually works:

1. An honest **coverage strip** so partial backfill reads as "in progress," not broken.
2. The **3D frequency-sheet view becomes the dashboard hero** and is made **fully
   interactive** — orbit/zoom/pan, per-core show/hide/solo, height-mode switch, reset.
3. A **porting board** below it — one read-only column per core listing recent merged
   fixes ("what was pushed"), plus a **drag-to-TODO** lane for tracking what needs
   carrying from one core to another. The TODO persists in `localStorage` (Mai writes
   nothing externally in v1).
4. The 3D height metric is **contrast-stretched client-side** so real divergence (all
   ratios bunch at 0.68–0.97) becomes legible; **color stays absolute severity**.

The flat heatmap remains the fast scan tool on `/drift/`.

## 2. Goals & Non-Goals

**Goals**
- A balanced **overview dashboard**: coverage → stats → 3D hero → porting board → secondary panels.
- A **fully-playable** 3D drift component (orbit/zoom/pan, layer toggle/solo, height modes, reset/top-down).
- A **porting board** with per-core "what landed" columns + a drag-and-drop personal TODO lane.
- **Contrast-stretched** height so real (high, similar) divergence ratios are readable; absolute-severity color.
- New **`pushes.json`** export of recent merged PRs per core, sourced by **extending the existing harvester**.
- Keep everything **static-Hugo-buildable** and **offline-renderable** (vendored 3D libs).

**Non-Goals**
- No backend writes; the porting TODO is browser-local until a future Cloudflare write-back.
- No JS framework — vanilla + Three.js (+ OrbitControls) only.
- Not redefining the bug/verification/drift pipeline; this consumes existing tables plus a PR harvest.
- Dragging *bugs* (vs merged PRs) onto the board is a later enhancement, not this spec.

## 3. Context & Constraints

- **Builds on** `web-design.md` (v1) and Plan 08 (`publish/{views,render,site,dataviz}.py`, `mai-data/` Hugo root). The dashboard layout and the 3D component are replaced/extended; the front-matter contract and dark-viz/light-chrome split carry over.
- **Data reality (2026-06-14 backfill):** 318 reports, 61 enriched (19%), 2 `fixed_confirmed`, 0 `likely_fixed`, **only core *three* backfilled**, 441 `drift_obs` rows across 6 fork pairs. The design must degrade gracefully and tell the truth about coverage.
- **Drift is real:** `drift_obs` comes from GitHub Trees blob-SHA comparison; `frequency.json` heights/colors are genuine. Raw `diverged/shared` ratios are all **0.68–0.97**, hence the normalization requirement.
- **A real bug was fixed en route:** `MAI_FREQ` was double-encoded as a JSON string by Hugo's `<script>`-context auto-escaping; fixed with `jsonify | safeJS`. The 3D hero now receives a real object.
- **Validated via the visual companion (2026-06-15):** balanced layout, 3D-as-hero, the playable control set, the porting board (drag tested), and the three height modes were all approved live.

## 4. Invariants (Non-Negotiable Rules)

1. **Static & offline-first.** Every page builds with `hugo` and renders without a backend. 3D libs are **vendored** (no hard CDN dependency). Viz degrades to a readable fallback if JSON/WebGL is missing.
2. **Read-only externally.** Mai writes nothing to GitHub/IPS. The porting TODO lives in `localStorage` only.
3. **Data, not duplication.** Every visual reads generated JSON; no hardcoded content. One source of truth (DB → publish → JSON/`.md`).
4. **Two channels, two meanings.** In the 3D view, **color = absolute severity**, **height = (mode-selected) normalized contrast**. They are never conflated.
5. **Honest coverage.** Partial backfill is shown as coverage/freshness, never hidden to fake completeness. Synthetic gaps (e.g. a subsystem not shared by a fork) are filled transparently and may be marked.
6. **Progressive enhancement.** Titles, stats, tables, and the porting columns are readable as plain HTML with no JS; orbit/drag/3D are layered on top.

## 5. System Architecture

```
 DB (report / enrichment / verification / correlation / drift_obs / merged PRs)
        │  publish (extends Plan 08 + dataviz)
        ▼
 mai-data/                         ← Hugo site root
   content/**.md                   ← pages (unchanged contract)
   data/frequency.json             ← RAW per-core/per-subsystem diverged/shared ratio (0..1)
   data/drift.json                 ← flat heatmap matrix (/drift/ scan view)
   data/dashboard.json             ← stats, top areas, recently fixed
   data/pushes.json                ← NEW: recent merged PRs per core
   static/js/vendor/three.min.js   ← NEW vendored
   static/js/vendor/OrbitControls.js ← NEW vendored
   static/js/frequency3d.js        ← REWRITTEN: playable component (orbit, modes, toggles)
   static/js/board.js              ← NEW: porting board drag/drop + localStorage
   static/css/mai.css              ← extended tokens/components (dashboard, board)
   layouts/index.html              ← RESTRUCTURED dashboard
   layouts/partials/*              ← coverage strip, stat tiles, 3D hero, board, panels
        │  hugo build
        ▼
 static site → Cloudflare Pages (later), behind Access
```

- **Server side** stays Python: harvester gains merged-PR fetch; `dataviz.py` gains `build_pushes`; `build_frequency` emits **raw ratios** (normalization moves to the client).
- **Client side** grows two components: the playable 3D (`frequency3d.js`) and the board (`board.js`). Both are vanilla, both fail soft.

## 6. Data Model (additions & changes)

- **`frequency.json` (changed):** `intensity[fork][subsystem]` becomes the **raw `diverged/shared` ratio in [0,1]** (drop the ×1.5 server scaling). Keep `cores[]` (name, full, y), `subsystems[]` (name, full, x, z). The client computes contrast/relative/absolute height and absolute-severity color from these raw ratios. Add `null` (not fill) where a subsystem is not shared for a fork; the client fills/marks gaps.
- **`pushes.json` (new):**
  ```json
  { "cores": [
      { "core": "three", "repo": "mangosthree/server",
        "pushes": [
          { "title": "...", "area": "Loot", "pr": 142,
            "url": "https://github.com/.../pull/142", "merged_at": "2026-06-..." }
        ] } ] }
  ```
  Up to N (≈8) most-recent merged PRs per tracked core. `area` via the existing `area_of` classifier on PR title/paths.
- **Merged-PR source records (new harvest scope):** extend the GitHub harvester to ingest recent **merged** PRs for **all four** core repos (today only *three* is populated). Stored as the existing `SourceRecord`/`Report` PR rows; `build_pushes` queries them. No new table.
- **No front-matter changes.** Bug/drift page contracts from v1 are unchanged.

## 7. Pipeline & Data Flow

Extends Plan 08's `publish_site`:
1. **Harvest** — issues + (now) recent merged PRs for every tracked core repo.
2. **Classify** — `area_of` tags each PR (reused, no new logic).
3. **Export viz data** — `dataviz.py` writes `drift.json`, `dashboard.json`, `frequency.json` (**raw ratios**), and **`pushes.json`**.
4. **Build** — Hugo compiles pages + partials + vendored JS; `frequency3d.js` reads `window.MAI_FREQ`, `board.js` fetches/embeds `pushes.json`.
   Deterministic and re-runnable, like every Mai stage.

## 8. Visual System (token additions to v1)

- Inherits v1 Primer tokens (light chrome, dark viz). Adds: **sticky top nav** with search; **coverage strip** (core chips on/off, enriched meter, freshness); **stat tiles** with accent edge + status hint; **board** components (columns, cards, TODO lane, drop-target highlight); 3D **layer overlay** panel (color dot, name, %, eye, solo).
- **Card** = a draggable fix: title, area pill, `core · PR #`. **TODO card** adds a `→ port to [core]` select and a **done** toggle.
- Dark panels: 3D hero and the `/drift/` heatmap. Everything else is light chrome.

## 9. Page Designs (approved)

**Dashboard (`/`), top to bottom:**
1. **Top nav** (Overview / Bugs / Drift / Cores + search).
2. **Coverage strip** — cores tracked vs backfilled, `enriched N/total` meter, freshness timestamp.
3. **Stat tiles** — Reports · Confirmed fixed (▲ ready to close) · Likely fixed · Open · Drift pairs.
4. **3D drift hero** — full-width, playable (see §10).
5. **Porting board** — see §11.
6. **Secondary row** — *Open bugs by area* (pills + bars) | *Recently confirmed fixed* (✔ list).

The per-core column headers link into each core; the old standalone "Cores" list is subsumed by the board columns.

## 10. The Playable 3D Frequency-Sheet Visualization

- **Component:** rewritten `frequency3d.js` using vendored Three.js r128 + **OrbitControls**. Reads `window.MAI_FREQ` (raw ratios).
- **Surfaces:** one wireframe `PlaneGeometry` per core, displaced per-vertex from an inverse-distance field over subsystem anchor points; `vertexColors` + `wireframe`. Closely stacked on Y.
- **Height modes (client-computed):**
  - **Contrast (default)** — global min→max stretch of raw ratios to [0,1].
  - **Relative** — per-subsystem centering on cross-core mean/σ; cores rise/sink vs siblings.
  - **Absolute** — raw ratio, the flat-plateau truth (kept to show *why* we normalize).
- **Color:** always **absolute severity** = `clamp((ratio-0.6)/0.4, 0, 1)` on green→amber→red.
- **Controls:** orbit (drag), zoom (scroll), pan (right-drag) via OrbitControls; per-core **toggle/solo** + "show all"; **auto-rotate** toggle; **peak-height** (default 1.8) and **spacing** (default 1.4) sliders; **subsystem guides** toggle (default on); **reset view** and **top-down**.
- **Robustness:** the `top`/`window.top` global-collision bug is fixed — all controls wired via explicit `getElementById`. WebGL absence → fallback message; `MAI_FREQ` null → fallback. Resize-aware; touch supported by OrbitControls.

## 11. Porting Board

- **Structure:** four read-only **per-core columns** (Zero / One / Two / Three), each listing recent merged fixes from `pushes.json` as cards (title · area pill · `core · PR #`). Plus **one "Porting TODO" lane**.
- **Interaction:** drag a card into the TODO lane → it becomes a TODO card with a **`→ port to [core]`** target picker (defaults to a sibling core) and a **done** toggle (strike-through). `×` removes it. De-dupe by `(core, pr)`.
- **Persistence:** `localStorage` key `mai.porting` (array of `{core, title, area, pr, target, done}`). Survives reload; personal to the browser. A future write-back can promote it to shared/team state.
- **Why one lane, not per-target columns:** 4 cores × 3 targets is too many columns; a single lane with per-card target tags is cleaner and matches "make a todo list."
- **Degradation:** with no `pushes.json` or no merged PRs for a core, the column shows an empty-state line; the TODO lane works regardless.

## 12. Interfaces & Contracts

- **`frequency.json`** — `{ cores[], subsystems[], intensity{fork:{subsystem: ratio|null}} }`, ratios in [0,1]. Client owns all normalization/coloring.
- **`pushes.json`** — schema in §6; the board validates shape and shows empty-states on mismatch.
- **`window.MAI_FREQ`** — injected via `{{ .Site.Data.frequency | jsonify | safeJS }}` (object, not string).
- **`localStorage["mai.porting"]`** — the board's only writable state; JSON array, versioned by shape.
- **Vendored JS** — `static/js/vendor/{three.min.js,OrbitControls.js}`; templates reference local paths, no runtime CDN.
- **CSS tokens** — one `mai.css`; templates reference classes, never inline colors (except data-driven viz).

## 13. Phased Build Plan

- **Phase 1 — Dashboard shell.** `mai.css` v2 additions; restructured `index.html` + partials (top nav, coverage strip, stat tiles, secondary panels). Static, no new JS. → the page reads right with the existing flat-heatmap hero temporarily.
- **Phase 2 — Playable 3D.** Vendor three + OrbitControls; `build_frequency` → raw ratios; rewrite `frequency3d.js` (orbit/zoom/pan, modes, toggle/solo, sliders, reset/top-down, fallbacks); swap it in as the hero. → the hero is interactive on real data.
- **Phase 3 — Porting board.** Extend harvester (merged PRs per core); `build_pushes` → `pushes.json`; board partial + `board.js` (drag/drop + localStorage). → the workspace is complete.
- **Phase 4 — Polish & verify.** Empty-states, graceful degradation, pytest for `build_pushes`/raw `build_frequency`/`area_of`-on-PRs; full `publish` + `hugo` build; visual check. → ship.

Each phase yields a viewable site.

## 14. Open Questions & Risks

| # | Item | Owner |
|---|---|---|
| 1 | Merged-PR harvest for non-three cores may be large/rate-limited; cap to recent N and a time window. | r-log |
| 2 | `localStorage` board is single-browser; revisit when Cloudflare write-back is scoped. | r-log / Antz |
| 3 | Vendored Three.js bumps repo size (~600 KB); acceptable for offline/Access reliability. | r-log |
| 4 | Relative mode on subsystems with tiny σ can exaggerate noise; clamp the height range. | r-log |
| 5 | "Two has no Bots subsystem" gaps — fill-with-mean vs hollow-node marker; default fill, mark later. | r-log |
| 6 | JS components aren't unit-tested; rely on build + manual visual check (note the coverage gap). | r-log |

## 15. Glossary & References

- **Porting board** — per-core "what landed" columns + a drag-to-TODO lane for cross-core porting.
- **Height mode** — Contrast / Relative / Absolute mapping of raw divergence ratio to surface height.
- **Absolute severity** — color channel; the true `diverged/shared` percentage, never normalized.
- **Coverage strip** — the honest "what Mai knows so far" ribbon (cores tracked, enriched %, freshness).
- Approved mockups: `.superpowers/brainstorm/<session>/content/` (dashboard-v2, drift3d-playable, drift3d-real).
- Builds on: `web-design.md`, `framework-architecture.md` §5/§7, Plan 08.
