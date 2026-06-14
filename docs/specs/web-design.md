---
title: "Mai — Web Design & Visualization"
status: Built
version: 1.0
owners: [r-log]
related:
  - _spec-template.md
  - framework-architecture.md
  - 08-publish-hugo.md
---

# Mai — Web Design & Visualization

> A GitHub/Primer-inspired visual redesign of the Mai Hugo site, plus the data-viz
> layer: area-tag label pills on every bug, a cross-core drift heatmap, and a 3D
> "frequency sheet" view (one heightmap surface per core). Light, dense, scannable
> content chrome; dark immersive panels for the visualizations. Pure Hugo + a small
> amount of client-side JS (Three.js) — no server-side rendering of graphics.

## 1. Summary

The current site (Plan 08) renders correct content with throwaway inline-CSS layouts.
This spec defines the real look: a GitHub-issues-style bug list and issue-detail page,
a dashboard whose centerpiece is cross-core drift, and a signature 3D "frequency sheet"
visualization on the sync page. It also defines the **area-tag** system the user asked
for (PR-style `[Movement]` labels) — as color-coded pills backed by a real per-bug area
classification. All of it is static Hugo output; the visualizations are client-side JS
fed by JSON that Hugo generates from the canonical DB.

## 2. Goals & Non-Goals

**Goals**
- A cohesive **GitHub/Primer-like** visual system (color, type, components) replacing the placeholder layouts.
- **Area tags as colored label pills** on every bug list row and detail page, color-coded by a fixed area palette.
- Polished **bug list** and **bug detail** pages.
- A **dashboard** with stat cards + a drift visual + verdict/area breakdowns.
- A **drift/sync page** whose hero is the **3D frequency-sheet** view, with a flat heatmap for quick scanning.
- Keep everything **static-Hugo-buildable** and **offline-renderable** for local dev.

**Non-Goals**
- No JS framework (React/Vue) — vanilla + Three.js only, kept minimal.
- No server-side graphics, no live/streaming data — the site is a static projection.
- No auth/theming controls in this phase (Cloudflare Access gates the whole site separately).
- Not redefining the data pipeline; this consumes existing tables.

## 3. Context & Constraints

- **Builds on Plan 08** (`publish/{views,render,site}.py`, `mai-data/` Hugo root). The renderers and layouts are replaced/extended; the publish orchestration and front-matter contract (v2) carry over and grow.
- **Data already exists**: `report`, `enrichment`, `verification`, `correlation`, `drift_obs`. The design adds one derived field (area) and JSON data exports for the viz.
- **Hugo is static**: 3D/heatmap are client-side JS shipped as assets; Hugo generates the backing JSON via its data pipeline / our generator.
- **Validated via live mockups** (visual companion, 2026-06-14): GitHub-like direction, pill area tags, the bug-detail layout, the dashboard, and the 3D frequency-sheet hero (tightly-stacked wireframe heightmaps) were all approved.

## 4. Invariants (Non-Negotiable Rules)

1. **Static & offline-first.** Every page builds with `hugo` and renders without a backend. Viz degrades gracefully if its JSON or CDN script is missing (show a fallback message, never a blank/broken page).
2. **Data, not duplication.** The viz reads generated JSON; it never hardcodes content. One source of truth (the DB → publish → JSON/`.md`).
3. **Front-matter is the contract.** Page `.md` front-matter (incl. `area`) is versioned and consumed by both Hugo templates and the viz; field renames are breaking.
4. **Theme discipline.** Light GitHub-like chrome for content; dark panels only for the embedded visualizations. One shared palette/token set, no per-page ad-hoc colors.
5. **Progressive enhancement.** Core content (titles, verdicts, tables) is readable as plain HTML even if no JS runs; 3D/heatmap are enhancements layered on top.

## 5. System Architecture

Three layers, same static-projection philosophy as the rest of Mai:

```
 DB (report/enrichment/verification/correlation/drift_obs)
        │  publish (extends Plan 08)
        ▼
 mai-data/                      ← Hugo site root
   content/**.md                ← pages (front-matter incl. area, verdict, …)
   data/drift.json              ← per-core/per-subsystem intensity (for 3D + heatmap)
   data/areas.json              ← area palette + counts
   assets/css/mai.css           ← Primer-like design tokens + components
   assets/js/heatmap.js         ← flat heatmap (canvas/DOM)
   assets/js/frequency3d.js     ← Three.js frequency-sheet viz
   layouts/**                   ← GitHub-style templates
        │  hugo build
        ▼
 static site  →  Cloudflare Pages (later), behind Access
```

- **Content pages** (list, detail) are server-rendered HTML from `.md` + layouts — fast, SEO-able, JS-free.
- **Visualizations** (`drift.json` → `frequency3d.js` / `heatmap.js`) are client-side, mounted into a container on the dashboard/sync pages.

## 6. Data Model (additions)

This phase adds **one derived concept — `area`** — and two **JSON exports**.

- **Area (derived, per report).** A canonical subsystem label for the pill, resolved by an `area_of(report)` classifier with precedence:
  1. IPS `sub_category` / `main_category` (from `raw_payload`) mapped to a canonical area;
  2. else enrichment `affected_entities` (npc/spell/zone/item/quest → Creature/Spell/World/Item/Quest);
  3. else GitHub PR touched-path → subsystem → area;
  4. else `Other`.
  Stored on the page front-matter as `area`; **derived & recomputable** (no schema migration — it's computed at publish time).
- **Area palette.** A fixed list (≈12) with stable colors: `Movement, Spell, Combat, Quest, Loot, Item, Creature, Character, World/Maps, Database, Build/Tools, Networking, Other`. Defined once; pills + bars + the 3D subsystem axis all use it.
- **`drift.json`** — `{ cores:[{name,id}], subsystems:[name], intensity:[[…]], divergence:{pair:{subsystem:pct}} }` — the heightfield + heatmap source, generated from `drift_obs` (+ per-core area counts for the sheet heights).
- **`areas.json`** — `{ area: {color, count} }` for the legend, top-areas bar, and pill colors.

## 7. Pipeline & Data Flow

Extends Plan 08's `publish_site`:
1. **Classify** — `area_of(report)` runs per bug; the result lands in front-matter `area`.
2. **Render** — templates emit GitHub-style pages; the area pill + verdict badge come from front-matter.
3. **Export viz data** — a new `publish/dataviz.py` writes `mai-data/data/drift.json` + `areas.json` from `drift_obs` + report/area counts.
4. **Build** — `hugo` compiles `.md` + layouts + assets; the viz JS loads the JSON at runtime.
   Re-runnable and deterministic, same as every other Mai stage.

## 8. Visual System (the "design tokens")

- **Type:** system font stack (`-apple-system, "Segoe UI", …`); 14px base, 1.5 line-height; 600 weight for titles.
- **Light chrome palette (Primer-ish):** text `#1f2328`, muted `#59636e`, border `#d1d9e0`, canvas `#f6f8fa`, accent blue `#0969da`, success `#1a7f37`, attention `#9a6700`, done `#8250df`, danger `#cf222e`.
- **Dark viz palette:** bg `#0a0e14`/`#0d1117`, grid `#30363d`, heat scale green `#2ea043` → amber `#d29922` → red `#f85149`.
- **Components:** `pill` (area, colored per palette), `badge` (verdict: green fixed / amber likely / gray open), status icons (✔ fixed / ◐ likely / ◯ open), `card`, `panel` (header + body), sidebar rows, stat cards.
- **Status→verdict mapping:** `fixed_confirmed` → green ✔; `likely_fixed` → amber ◐; `open` → gray ◯.

## 9. Page Designs (approved mockups)

- **Bug list** (`/<core>/`): GitHub-issues rows — status icon · **area pill** · linked title · verdict badge (right) · meta line (id · zone · reporter/PR). Top filter bar (`area:Movement verdict:open`). Counts header.
- **Bug detail** (`/<core>/bugs/<id>/`): title + area pill + verdict badge; a prominent **verdict box** that leads with the evidence ("confirmed by merged PR #N · explicit ref + 0.64 sim"); body = **AI summary → steps → affected → collapsed original report**; **right sidebar** = area, core, verdict+confidence, linked PR (merged), source link back to getmangos.eu, reporter, zone.
- **Dashboard** (`/`): row of **stat cards** (reports/fixed/likely/open/cores) · **drift visual** centerpiece · **Verdicts** + **Top areas** bar panels · **Recently confirmed fixed** strip · links into each core.
- **Sync / drift page** (`/sync/`): **hero = 3D frequency sheets**; below it the **flat heatmap** (forks×forks, click a cell → subsystem table) for quick scanning; per-pair subsystem tables.

## 10. The 3D Frequency-Sheet Visualization

- **Concept:** one **wireframe heightmap surface per core**, stacked closely on the Y axis. Surface height at each (x,z) = that core's **intensity** (bug/drift count) for the subsystem nearest that point; vertices heat-colored low→high (green→red). Faint vertical guide lines mark subsystem columns so the eye correlates the same spot across cores.
- **Tech:** Three.js (r128, global build via CDN with a vendored fallback), `PlaneGeometry` displaced per-vertex from `drift.json`, `vertexColors` + `wireframe`, slow auto-rotate + drag-to-rotate, resize-aware. ~5k verts × 4 cores — trivial perf.
- **Role:** the showcase "drift galaxy," paired with the flat heatmap (the fast daily-driver read). Static-until-interacted is acceptable; default is gentle auto-rotation.
- **Metric is configurable** at generation time (bug count / drift % / change frequency) — default: per-core per-subsystem **drift-weighted bug intensity**.

## 11. Interfaces & Contracts

- **Front-matter (v2+):** `schema_version, id, title, core, status, verdict, confidence, area, sources[], …`. `area` is the new required field for bug pages.
- **`drift.json` / `areas.json` schemas** (§6) are the viz contract — versioned; the JS validates shape and shows a fallback on mismatch.
- **CSS tokens** live in one `assets/css/mai.css`; templates reference classes, never inline colors (except the data-driven viz).
- **Hugo layout hierarchy:** `baseof` → `single` / `list` / `index` / a dedicated `sync` layout that mounts the viz container.

## 12. Phased Build Plan

- **Phase A — Visual system + content pages.** `mai.css` (tokens + components), GitHub-style `baseof/list/single`, the **area classifier** (`area_of`) + front-matter `area`, area pills + verdict badges + status icons. (Offline, no JS viz.) → the list + detail pages look right.
- **Phase B — Dashboard + flat heatmap.** Stat cards, verdict/top-area bars, `dataviz.py` → `drift.json`/`areas.json`, `heatmap.js` (DOM/canvas, dark-neon cells). → the dashboard + quick-scan heatmap.
- **Phase C — 3D frequency sheets.** `frequency3d.js` (Three.js), the `sync` layout hero, graceful fallback. → the showcase viz.
- Each phase ships a working, viewable site; A is the MVP.

## 13. Open Questions & Risks

| # | Item | Owner |
|---|---|---|
| 1 | Final area taxonomy list + exact pill colors (≈12 areas) — confirm names. | r-log |
| 2 | Frequency-sheet **metric** default (bug count vs drift% vs blend). | r-log |
| 3 | Three.js delivery: CDN vs vendored copy in `assets/` (offline + Access reliability). Lean: vendor it. | r-log |
| 4 | Dark-everywhere vs light-chrome+dark-viz (current decision: split). Confirm. | r-log / Antz |
| 5 | Stale-page pruning (carried from Plan 08) before the redesign ships publicly. | r-log |

## 14. Glossary

- **Area** — canonical subsystem label shown as a colored pill (e.g. `Movement`).
- **Frequency sheet** — a per-core wireframe heightmap surface; height = intensity.
- **Verdict box** — the lead callout on a bug page stating fixed/likely/open + evidence.
- **Chrome** — the light GitHub-like content UI (as opposed to the dark viz panels).

## 15. References

- Approved mockups: `mai/.superpowers/brainstorm/<session>/content/` (bug-list, bug-detail, dashboard, heatmap-cool, 3d-frequency-v2).
- Primer (GitHub design system) — visual reference, not a dependency.
- Three.js — client-side 3D library for the frequency-sheet viz.
- Builds on: `framework-architecture.md` §5/§7, Plan 08 (`08-publish-hugo.md`).
