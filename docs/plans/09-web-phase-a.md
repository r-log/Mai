# Web Redesign Phase A — Visual System + Area Pills Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the Mai site a GitHub/Primer-like visual system and put a color-coded **area pill** (the `[Movement]`-style tag) on every bug — via a per-bug area classifier, `area` front-matter, a real stylesheet, and GitHub-style list/detail layouts.

**Architecture:** A pure-Python `area_of()` classifier (IPS category → enrichment entities → title keywords → Other) runs in the publish `views` layer and lands as `area` front-matter. The page chrome (status icon, area pill, verdict badge, title header, sidebar) moves into Hugo templates that read front-matter; the markdown body keeps only the rich content (summary/steps/affected/evidence). A single `static/css/mai.css` holds Primer-like tokens + components, including per-area pill colors.

**Tech Stack:** Python 3.12 · SQLAlchemy 2.0 async · pytest + pytest-asyncio · Hugo (templates + static CSS).

---

## Builds on Plans 01–08 (esp. Plan 08 publish)

Reuse as-is (do NOT redefine): `mai.db.models`, `mai.repository.*`, `mai.publish.views.ReportBundle` (extended here), `mai.publish.render`, `mai.publish.site`, `tests/conftest.py`. The publish orchestration and front-matter v2 contract carry over and grow (`area` added).

**Design principles (spec §4):** static & offline-first; front-matter is the contract; templates own chrome, the body owns content; one shared CSS token set.

## File Structure

```
src/mai/publish/
  areas.py              # NEW: AREAS palette + area_of() classifier
  views.py              # MODIFY: ReportBundle.area + compute it in report_bundle
  render.py             # MODIFY: emit `area:` front-matter; body drops title/verdict line
mai-data/
  static/css/mai.css    # NEW: Primer-like tokens + components + per-area pill colors
  layouts/_default/baseof.html   # MODIFY: topbar nav + link mai.css
  layouts/_default/list.html     # MODIFY: GitHub-issues rows
  layouts/_default/single.html   # MODIFY: header + verdict callout + sidebar + content
tests/
  test_areas.py
  (test_publish_views.py, test_publish_render.py — updated)
```

---

### Task 1: Area taxonomy + classifier

**Files:**
- Create: `mai/src/mai/publish/areas.py`
- Create: `mai/tests/test_areas.py`

- [ ] **Step 1: Write the failing test**

`mai/tests/test_areas.py`:

```python
from mai.publish.areas import AREAS, area_of


def test_areas_palette_has_other_and_unique_slugs():
    slugs = [a["slug"] for a in AREAS]
    assert "other" in slugs
    assert len(slugs) == len(set(slugs))  # no dup slugs
    assert all(a.get("color") for a in AREAS)  # every area has a color


def test_area_from_ips_subcategory():
    # IPS sub-category "Pet" -> Creature
    assert area_of("Some title", None, {"sub_category": "Pet"}) == "Creature"
    assert area_of("x", None, {"sub_category": "Movement"}) == "Movement"


def test_area_from_enrichment_entities_when_no_category():
    enr = {"affected_entities": {"spell": ["Holy Light"], "npc": []}}
    assert area_of("ambiguous", enr, {}) == "Spell"


def test_area_from_title_keywords():
    assert area_of("Far teleport leaves player airborne", None, {}) == "Movement"
    assert area_of("Darkshore quest chain breaks", None, {}) == "Quest"


def test_area_defaults_to_other():
    assert area_of("totally unrelated wording", None, {}) == "Other"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mai && pytest tests/test_areas.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mai.publish.areas'`

- [ ] **Step 3: Write `publish/areas.py`**

```python
# Canonical area palette (GitHub-label style: light bg + readable text).
AREAS = [
    {"name": "Movement",  "slug": "movement",  "color": "#0969da"},
    {"name": "Spell",     "slug": "spell",     "color": "#8250df"},
    {"name": "Combat",    "slug": "combat",    "color": "#cf222e"},
    {"name": "Quest",     "slug": "quest",     "color": "#1a7f37"},
    {"name": "Loot",      "slug": "loot",      "color": "#9a6700"},
    {"name": "Item",      "slug": "item",      "color": "#bc4c00"},
    {"name": "Creature",  "slug": "creature",  "color": "#bf3989"},
    {"name": "Character", "slug": "character", "color": "#6639ba"},
    {"name": "World",     "slug": "world",     "color": "#0c7489"},
    {"name": "Database",  "slug": "database",  "color": "#57606a"},
    {"name": "Tools",     "slug": "tools",     "color": "#424a53"},
    {"name": "Network",   "slug": "network",   "color": "#4f46c4"},
    {"name": "Other",     "slug": "other",     "color": "#59636e"},
]

# First keyword match wins; order matters.
_KEYWORDS = [
    ("Movement",  ["movement", "teleport", "speed", "fly", "mount", "fall", "jump",
                   "navi", "mmap", "pathfind", "waypoint"]),
    ("Spell",     ["spell", "aura", "cast", "mana", "cooldown", "rune", "proc", "buff"]),
    ("Combat",    ["combat", "damage", "melee", "threat", "aggro", "agro", "crit",
                   "resil", "pvp", "block", "parry"]),
    ("Loot",      ["loot", "lootable", "drop ", "corpse", "skinning"]),
    ("Quest",     ["quest", "objective", "gossip", "escort"]),
    ("Item",      ["item", "equip", "enchant", "inventory", "bag", "gem"]),
    ("Creature",  ["creature", "npc", "mob", "pet", "beast", "tame", "vendor",
                   "trainer", "guard", "devilsaur"]),
    ("Character", ["character", "level", "race", "class", "talent", "experience",
                   "starting", "stat"]),
    ("World",     ["zone", "area", "map", "vmap", "instance", "raid", "dungeon",
                   "gameobject", "transport", "tram", "elevator"]),
    ("Database",  ["database", "sql", "db_version", "table"]),
    ("Tools",     ["extractor", "cmake", "compile", "build", "dbc editor", "tool"]),
    ("Network",   ["packet", "opcode", "socket", "realmd", "login", "disconnect"]),
]

_ENTITY_AREA = [("spell", "Spell"), ("npc", "Creature"), ("quest", "Quest"),
                ("item", "Item"), ("zone", "World")]


def _match_keywords(text: str) -> str | None:
    text = (text or "").lower()
    for area, kws in _KEYWORDS:
        if any(kw in text for kw in kws):
            return area
    return None


def area_of(title: str, enrichment: dict | None, source_payload: dict) -> str:
    """Classify a bug into a canonical area. Precedence: IPS category, then
    enrichment entities, then title keywords, then Other."""
    cat = " ".join(str(source_payload.get(k, "") or "")
                   for k in ("sub_category", "main_category"))
    hit = _match_keywords(cat)
    if hit:
        return hit
    if enrichment:
        entities = enrichment.get("affected_entities") or {}
        for key, area in _ENTITY_AREA:
            if entities.get(key):
                return area
    hit = _match_keywords(title)
    if hit:
        return hit
    return "Other"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd mai && pytest tests/test_areas.py -v`
Expected: PASS (5 passed)

- [ ] **Step 5: Commit**

```bash
git add mai/src/mai/publish/areas.py mai/tests/test_areas.py
git commit -m "feat: area taxonomy + per-bug area classifier"
```

---

### Task 2: Wire `area` into the publish bundle + front-matter

**Files:**
- Modify: `mai/src/mai/publish/views.py` (`ReportBundle.area` + compute in `report_bundle`)
- Modify: `mai/src/mai/publish/render.py` (emit `area:`; drop body title/verdict line)
- Modify: `mai/tests/test_publish_views.py` (assert area)
- Modify: `mai/tests/test_publish_render.py` (assert area front-matter; title not in body)

- [ ] **Step 1: Update the views test (add area assertion)**

In `mai/tests/test_publish_views.py`, the seed ingests `ips:r1` titled `"Pet bug"`. Add to `test_report_bundle_gathers_everything` (after the existing asserts):

```python
    assert b.area == "Creature"  # "Pet bug" title -> pet -> Creature
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd mai && pytest tests/test_publish_views.py::test_report_bundle_gathers_everything -v`
Expected: FAIL with `AttributeError: 'ReportBundle' object has no attribute 'area'`

- [ ] **Step 3: Add `area` to `ReportBundle` and compute it in `report_bundle`**

In `mai/src/mai/publish/views.py`: add the import, the dataclass field, and the computation.

Add to imports:
```python
from sqlalchemy import and_, desc, func, select   # ensure `desc` is imported
from mai.db.models import (
    DriftObservation, Enrichment, Report, ReportSourceMap, SourceRecord, Verification,
)
from mai.publish.areas import area_of
```

Add the field to the dataclass:
```python
@dataclass
class ReportBundle:
    report: Report
    enrichment: dict | None
    verification: Verification | None
    correlations: list[tuple[str, str, float]]
    area: str
```

In `report_bundle`, before the `return`, gather the latest source payload and classify:
```python
    payload: dict = {}
    maps = list(await session.scalars(
        select(ReportSourceMap).where(ReportSourceMap.report_id == report.id)))
    for m in maps:
        rec = await session.scalar(
            select(SourceRecord)
            .where(SourceRecord.source_type == m.source_type,
                   SourceRecord.source_id == m.source_id)
            .order_by(desc(SourceRecord.version)).limit(1))
        if rec is not None:
            payload = rec.payload
            break
    area = area_of(report.title, enr.result if enr else None, payload)
    return ReportBundle(report=report, enrichment=enr.result if enr else None,
                        verification=ver, correlations=corrs, area=area)
```
(Remove the old `return ReportBundle(...)` line; this replaces it. `and_` may be unused — only add it if not already present and remove if your linter complains.)

- [ ] **Step 4: Run the views test**

Run: `cd mai && pytest tests/test_publish_views.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Update the render test**

Replace `mai/tests/test_publish_render.py`'s `_bundle` helper and the two report-page tests with:

```python
def _bundle(**kw):
    base = dict(
        report=Report(canonical_key="ips:r1", core="zero", title="Raw title", status="completed"),
        area="Loot",
        enrichment={"normalized_title": "Pet threat", "english_summary": "Pet loses threat.",
                    "steps_to_reproduce": ["attack", "send pet"],
                    "affected_entities": {"npc": ["Devilsaur"], "zone": []}},
        verification=Verification(report_id="x", verdict="fixed_confirmed", confidence=0.95,
                                  evidence=[]),
        correlations=[("gh_pr:zero/server#7", "explicit_ref", 1.0)])
    base.update(kw)
    return ReportBundle(**base)


def test_render_report_page_full():
    md = render_report_page(_bundle())
    assert md.startswith("---\n")
    assert "schema_version: 2" in md
    assert 'id: "ips:r1"' in md
    assert 'title: "Pet threat"' in md
    assert "area: Loot" in md
    assert "verdict: fixed_confirmed" in md
    assert "confidence: 0.95" in md
    assert "## Summary" in md and "Pet loses threat." in md
    assert "## Steps to reproduce" in md and "- attack" in md
    assert "**npc:** Devilsaur" in md
    assert "## Evidence" in md and "`gh_pr:zero/server#7`" in md
    assert "# Pet threat" not in md          # title is template chrome now, not body
    assert "**Verdict:**" not in md          # verdict is template chrome now


def test_render_report_page_minimal_falls_back_to_raw_title():
    md = render_report_page(_bundle(enrichment=None, verification=None, correlations=[]))
    assert 'title: "Raw title"' in md
    assert "verdict: open" in md
    assert "area: Loot" in md
    assert "## Summary" not in md
```

(Leave `test_render_drift_page_sorts_by_diverged` and `test_render_home_shows_counts` unchanged.)

- [ ] **Step 6: Run it to verify it fails**

Run: `cd mai && pytest tests/test_publish_render.py -v`
Expected: FAIL (current render still emits `# title` / `**Verdict:**` and no `area:`).

- [ ] **Step 7: Update `render_report_page` in `render.py`**

Replace the whole `render_report_page` function with:

```python
def render_report_page(bundle: ReportBundle) -> str:
    r = bundle.report
    enr = bundle.enrichment or {}
    title = enr.get("normalized_title") or r.title or r.canonical_key
    ver = bundle.verification
    verdict = ver.verdict if ver else "open"
    confidence = ver.confidence if ver else 0.0

    lines = ["---", f"schema_version: {SCHEMA_VERSION}", f"id: {_q(r.canonical_key)}",
             f"title: {_q(title)}", f"core: {r.core}", f"area: {bundle.area}",
             f"status: {r.status}", f"verdict: {verdict}", f"confidence: {confidence}",
             "---", ""]

    summary = enr.get("english_summary")
    if summary:
        lines += ["## Summary", "", summary, ""]
    steps = enr.get("steps_to_reproduce") or []
    if steps:
        lines += ["## Steps to reproduce", ""] + [f"- {s}" for s in steps] + [""]
    entities = enr.get("affected_entities") or {}
    ent_lines = [f"- **{k}:** {', '.join(v)}" for k, v in entities.items() if v]
    if ent_lines:
        lines += ["## Affected", ""] + ent_lines + [""]
    if bundle.correlations:
        lines += ["## Evidence", ""]
        lines += [f"- `{key}` ({method}, score {score:.2f})"
                  for key, method, score in bundle.correlations]
        lines += [""]
    return "\n".join(lines).rstrip() + "\n"
```

- [ ] **Step 8: Run the render + full publish tests**

Run: `cd mai && pytest tests/test_publish_render.py tests/test_publish_site.py -v`
Expected: PASS (`test_publish_site` still passes — it asserts the raw `title` text which is now in front-matter `title:`).

- [ ] **Step 9: Commit**

```bash
git add mai/src/mai/publish/views.py mai/src/mai/publish/render.py mai/tests/test_publish_views.py mai/tests/test_publish_render.py
git commit -m "feat: classify + emit area front-matter; move title/verdict to template chrome"
```

---

### Task 3: The stylesheet (`mai.css`)

**Files:**
- Create: `mai/mai-data/static/css/mai.css`

(Static asset — Hugo copies `static/` verbatim to `/css/mai.css`. No tests; verified by the build smoke in Task 5.)

- [ ] **Step 1: Write `mai-data/static/css/mai.css`**

```css
:root{
  --fg:#1f2328; --muted:#59636e; --border:#d1d9e0; --canvas:#f6f8fa; --bg:#fff;
  --blue:#0969da; --green:#1a7f37; --amber:#9a6700; --purple:#8250df; --red:#cf222e;
}
*{box-sizing:border-box}
body{margin:0;font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:var(--fg);background:var(--bg)}
a{color:var(--blue);text-decoration:none}a:hover{text-decoration:underline}
.container{max-width:1080px;margin:0 auto;padding:22px 18px}
.muted{color:var(--muted)}

/* top bar */
.topbar{display:flex;align-items:center;gap:18px;padding:12px 18px;border-bottom:1px solid var(--border);background:var(--canvas)}
.topbar .brand{font-weight:700;color:var(--fg)}
.topbar nav a{color:var(--fg);margin-right:14px;font-weight:500}

/* pills (area) */
.pill{display:inline-block;font-size:12px;font-weight:600;line-height:20px;padding:0 8px;border-radius:999px;
  border:1px solid #0000001a;background:#eaeef2;color:var(--muted);margin-right:6px;vertical-align:1px}
.pill.area-movement{background:#ddf4ff;color:#0969da}.pill.area-spell{background:#fbefff;color:#8250df}
.pill.area-combat{background:#ffebe9;color:#cf222e}.pill.area-quest{background:#dafbe1;color:#1a7f37}
.pill.area-loot{background:#fff8c5;color:#7a5c00}.pill.area-item{background:#ffe7d1;color:#bc4c00}
.pill.area-creature{background:#ffeff7;color:#bf3989}.pill.area-character{background:#efe9fb;color:#6639ba}
.pill.area-world{background:#d3f3f7;color:#0c7489}.pill.area-database{background:#eaeef2;color:#57606a}
.pill.area-tools{background:#eaecef;color:#424a53}.pill.area-network{background:#e8e6ff;color:#4f46c4}
.pill.area-other{background:#eaeef2;color:#59636e}

/* verdict badges */
.badge{font-size:12px;font-weight:600;padding:1px 9px;border-radius:999px}
.b-fixed_confirmed{background:#dafbe1;color:#1a7f37}
.b-likely_fixed{background:#fff8c5;color:#9a6700}
.b-open{background:#eaeef2;color:#59636e}

/* status icon */
.ico{width:18px;flex:0 0 18px;text-align:center;font-size:15px}
.v-fixed_confirmed{color:#8250df}.v-likely_fixed{color:#9a6700}.v-open{color:#1a7f37}

/* issue list (GitHub-style rows) */
.list-head{display:flex;align-items:baseline;gap:10px;margin-bottom:10px}
.list-head h1{font-size:20px;margin:0}
.issue-list{border:1px solid var(--border);border-radius:8px;overflow:hidden}
.ghrow{display:flex;gap:10px;align-items:flex-start;padding:11px 14px;border-bottom:1px solid #eaeef2}
.ghrow:last-child{border-bottom:0}
.ghrow-main{flex:1;min-width:0}
.ghrow-title{font-weight:600;color:var(--fg)}
.ghrow-title:hover{color:var(--blue);text-decoration:none}
.ghrow .badge{float:right;margin-left:8px}
.ghrow-meta{color:var(--muted);font-size:12px;margin-top:3px}

/* issue detail */
.issue-title{font-size:24px;font-weight:600;margin:0 0 4px}
.issue-sub{color:var(--muted);font-size:13px;border-bottom:1px solid var(--border);padding-bottom:12px;margin-bottom:16px}
.issue-grid{display:flex;gap:22px;align-items:flex-start}
.issue-main{flex:1;min-width:0}
.issue-side{flex:0 0 220px;font-size:13px}
.issue-side .srow{padding:8px 0;border-bottom:1px solid #eaeef2;display:flex;justify-content:space-between;gap:8px}
.issue-side .k{font-weight:600}
.callout{border-radius:8px;padding:10px 14px;margin-bottom:16px;font-size:13px;border:1px solid}
.callout.c-fixed_confirmed{background:#dafbe1;border-color:#1a7f37;color:#1a7f37}
.callout.c-likely_fixed{background:#fff8c5;border-color:#9a6700;color:#7a5c00}
.callout.c-open{background:#f6f8fa;border-color:#d1d9e0;color:#59636e}
.issue-main h2{font-size:14px;text-transform:uppercase;letter-spacing:.03em;color:var(--muted);margin:18px 0 6px}
.issue-main code{background:var(--canvas);border:1px solid var(--border);border-radius:6px;padding:0 5px;font-size:12px}

/* tables (drift pages) */
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{border:1px solid var(--border);padding:5px 9px;text-align:left}
th{background:var(--canvas)}
```

- [ ] **Step 2: Commit**

```bash
git add mai/mai-data/static/css/mai.css
git commit -m "feat: GitHub-like stylesheet (tokens, pills, badges, list/detail components)"
```

---

### Task 4: GitHub-style layouts

**Files:**
- Modify: `mai/mai-data/layouts/_default/baseof.html`
- Modify: `mai/mai-data/layouts/_default/list.html`
- Modify: `mai/mai-data/layouts/_default/single.html`

- [ ] **Step 1: Replace `baseof.html`**

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{ .Title }} · Mai</title>
  <link rel="stylesheet" href="/css/mai.css">
</head>
<body>
  <header class="topbar">
    <a class="brand" href="/">🐛 Mai</a>
    <nav><a href="/">Dashboard</a><a href="/sync/">Drift</a></nav>
  </header>
  <main class="container">{{ block "main" . }}{{ end }}</main>
</body>
</html>
```

- [ ] **Step 2: Replace `list.html`**

```html
{{ define "main" }}
  <div class="list-head"><h1>{{ .Title }}</h1><span class="muted">{{ len .Pages }} items</span></div>
  {{ .Content }}
  <div class="issue-list">
  {{ range .Pages }}
    {{ if .Params.verdict }}
      <div class="ghrow">
        <span class="ico v-{{ .Params.verdict }}">{{ if eq .Params.verdict "fixed_confirmed" }}&#10004;{{ else if eq .Params.verdict "likely_fixed" }}&#9680;{{ else }}&#9711;{{ end }}</span>
        <div class="ghrow-main">
          <span class="badge b-{{ .Params.verdict }}">{{ .Params.verdict }}</span>
          <a class="ghrow-title" href="{{ .RelPermalink }}"><span class="pill area-{{ lower .Params.area }}">{{ .Params.area }}</span>{{ .Title }}</a>
          <div class="ghrow-meta">{{ .Params.id }} · {{ .Params.core }}</div>
        </div>
      </div>
    {{ else }}
      <div class="ghrow"><a href="{{ .RelPermalink }}">{{ .Title }}</a></div>
    {{ end }}
  {{ end }}
  </div>
{{ end }}
```

- [ ] **Step 3: Replace `single.html`**

```html
{{ define "main" }}
  {{ if .Params.verdict }}
    <h1 class="issue-title">
      <span class="pill area-{{ lower .Params.area }}">{{ .Params.area }}</span>{{ .Title }}
      <span class="badge b-{{ .Params.verdict }}" style="font-size:13px">{{ .Params.verdict }}</span>
    </h1>
    <div class="issue-sub">{{ .Params.core }} · {{ .Params.id }} · status {{ .Params.status }}</div>
    <div class="issue-grid">
      <div class="issue-main">
        <div class="callout c-{{ .Params.verdict }}"><b>{{ .Params.verdict }}</b> — confidence {{ .Params.confidence }}</div>
        {{ .Content }}
      </div>
      <aside class="issue-side">
        <div class="srow"><span class="k">Area</span><span class="pill area-{{ lower .Params.area }}">{{ .Params.area }}</span></div>
        <div class="srow"><span class="k">Core</span><span>{{ .Params.core }}</span></div>
        <div class="srow"><span class="k">Status</span><span>{{ .Params.status }}</span></div>
        <div class="srow"><span class="k">Verdict</span><span>{{ .Params.verdict }}</span></div>
        <div class="srow"><span class="k">Confidence</span><span>{{ .Params.confidence }}</span></div>
        <div class="srow"><span class="k">Source</span><span>{{ .Params.id }}</span></div>
      </aside>
    </div>
  {{ else }}
    <h1>{{ .Title }}</h1>
    {{ .Content }}
  {{ end }}
{{ end }}
```

- [ ] **Step 4: Build smoke (needs `hugo`; skip+note if absent)**

Run: `cd mai && python -m mai.cli.__main__ init-db && python -c "import asyncio; from mai.db.session import SessionFactory; from mai.contracts import IntakeEvent; from mai.ingest import ingest_event;\nasyncio.run((lambda: None)())" 2>/dev/null; rm -rf mai-data/content mai-data/public && python -m mai.cli.__main__ init-db`
Then seed one bug + publish + build:
```bash
cd mai && python -c "
import asyncio
from mai.db.session import SessionFactory
from mai.contracts import IntakeEvent
from mai.ingest import ingest_event
async def go():
    async with SessionFactory() as s:
        await ingest_event(s, IntakeEvent('ips','r1','Far teleport leaves player airborne','three',raw_payload={'markdown':'x'}))
        await s.commit()
asyncio.run(go())
" && python -m mai.cli.__main__ publish && (cd mai-data && hugo --quiet && echo BUILT) && grep -o 'area-movement' mai-data/public/three/bugs/ips-r1/index.html | head -1
```
Expected: prints `BUILT` and `area-movement` (the Movement pill rendered for that title). If `hugo` is not installed, SKIP the build and just confirm `mai-data/content/three/bugs/ips-r1.md` contains `area: Movement`.

- [ ] **Step 5: Commit**

```bash
git add mai/mai-data/layouts/
git commit -m "feat: GitHub-style baseof/list/single layouts with area pills + verdict badges"
```

---

### Task 5: Full suite green + populated rebuild

**Files:** none (integration only).

- [ ] **Step 1: Run the full test suite**

Run: `cd mai && pytest -q`
Expected: PASS (90 passed — 85 prior + 5 new area tests; the views/render test edits are modifications, not additions).

- [ ] **Step 2: Rebuild the populated site (if the backfill DB exists)**

If `mai.db` from the backfill is present:
```bash
cd mai && python -m mai.cli.__main__ publish && (cd mai-data && hugo --quiet && echo BUILT)
```
Expected: prints `BUILT`; open `mai-data/public/three/bugs/ips-r1831/index.html` — the Loot pill + `fixed_confirmed` badge + verdict callout render with the new CSS. (If `mai.db` was cleared, re-run the backfill first; this step is just visual confirmation.)

- [ ] **Step 3: Commit (if any uncommitted regenerated scaffolding remains — should be none, content is git-ignored)**

```bash
git status --short
```
Expected: clean (generated `mai-data/content` + `public` are git-ignored).

---

## Self-Review

- **Spec coverage:** Implements spec §6 (area derivation + front-matter), §8 (design tokens / `mai.css`), §9 bug-list + bug-detail page designs, §11 front-matter `area` contract + CSS-token discipline + Hugo layout hierarchy. This is spec §12 **Phase A** (the MVP). Dashboard/heatmap (Phase B) and 3D (Phase C) are explicitly out of scope here.
- **Invariants:** static & offline (no JS needed for these pages) ✓ · front-matter is the contract (`area` added, consumed by templates) ✓ · templates own chrome / body owns content ✓ · one CSS token set, per-area colors centralized ✓ · graceful (pages render as plain HTML) ✓.
- **Deferred (noted):** richer sidebar (linked-PR/source-URL/reporter needs more front-matter) and the `static/` vs `assets/` pipeline choice — this plan uses `static/css/mai.css` (simpler than Hugo Pipes; spec §3 allows it). The dashboard `index.html` keeps Plan 08's markup, now styled by `mai.css`.
- **Placeholder scan:** none — every step has runnable code/commands.
- **Type consistency:** `area_of(title, enrichment, source_payload)` signature matches the call in `report_bundle`; `ReportBundle.area` (new field) is set in `report_bundle` and read in `render_report_page` (`bundle.area`) and the templates (`.Params.area`); verdict class names (`b-`/`c-`/`v-` + `fixed_confirmed|likely_fixed|open`) match across CSS and both templates.

## Notes for later plans

- **Phase B** (dashboard + flat heatmap): `dataviz.py` → `data/drift.json`/`areas.json`, stat cards, `heatmap.js`, redesigned `index.html`.
- **Phase C** (3D): `frequency3d.js` (Three.js, vendored) + a `sync` section layout mounting the viz.
- **Richer sidebar:** add `source_url`, `reporter`, `linked_prs` to front-matter (from raw payload + correlations) to fill the detail sidebar like the mockup.
- **Area QA:** the keyword classifier is heuristic; after a real publish, eyeball the `Other` bucket and tune `_KEYWORDS`.
