# Port-Debt Board Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Put the engine's open `PortCandidate` backlog on screen as a usable static board — a new `/port/` page with per-target-fork columns of quick-wins-first cards (title, source link, tier, evidence) and a personal `localStorage` triage overlay.

**Architecture:** One additive server export (`build_port_candidates` → `port_candidates.json`) plus a new Hugo page (`content/port/_index.md` + `layouts/port/list.html`) and one vanilla script (`static/js/portboard.js`), mirroring the existing `build_pushes`/`board.js` pattern. No backend; triage is `localStorage` only.

**Tech Stack:** Python 3.12 · async SQLAlchemy 2.0 · pytest (server) · Hugo + vanilla JS + existing Primer CSS (client). No new dependency.

## Global Constraints

From `docs/specs/port-debt-board.md`:
- **Read-only externally.** The board writes nothing to GitHub or the engine DB; triage is `localStorage` only.
- **Static & offline-first.** `/port/` builds with `hugo` and degrades to empty-states if JSON/JS/`localStorage` is missing. `window.MAI_PORT` is embedded via `{{ .Site.Data.port_candidates | jsonify | safeJS }}` (NOT a quoted string).
- **Engine owns truth; client owns intent.** Only `status == "open"` candidates are exported; the client only records `working`/`done`/`dismissed`.
- **Stable ids, sticky + self-cleaning.** Card id = `f"{patch_group_id}:{target_core}"`; triage persists by id and prunes ids absent from the current export on load.
- **Card sort:** within a column, tier rank `surgical<small<moderate<bulk`, then `magnitude` ascending.
- **Don't touch** the existing dashboard board (`board.js`/`pushes.json`).
- **Match the stack:** 4-space indent (Python); compact vanilla-JS IIFE like `board.js`; `feat:`-style commits; **no AI attribution**.

---

## Builds on existing code

Reuse as-is (do not redefine):
- `mai.db.models` — `PortCandidate(patch_group_id, source_core, target_core, subsystem, classification, magnitude, tier, confidence, evidence, status, source_sha)`, `PatchGroup(id, patch_id)`, `Commit(core, sha, message)`, `Repo(core, full_name)`.
- `mai.publish.dataviz` — `build_pushes` is the grouping/joining model; `write_dataviz(session, out_dir)` writes each `data/*.json`. The `publish` CLI already calls the publish pipeline.
- Hugo conventions: `layouts/sync/list.html` uses `{{ define "main" }}…{{ end }}` and `{{ .Site.Data.X | jsonify | safeJS }}`; `static/js/board.js` is the vanilla render+`localStorage` idiom (`esc()` helper, IIFE, `localStorage` load/save/render).
- `tests/conftest.py` — async in-memory sqlite `session` fixture.

## File Structure

```
src/mai/publish/dataviz.py          # MODIFY: build_port_candidates + _source_repos + write line
mai-data/
  content/port/_index.md            # NEW: the /port/ section page (front matter)
  layouts/port/list.html            # NEW: page shell + MAI_PORT embed
  static/js/portboard.js            # NEW: render columns/cards, filters, evidence, triage
  static/css/mai.css                # MODIFY: .port-* components
  layouts/partials/<nav>            # MODIFY (Task 4): add a "Port" nav link
tests/
  test_port_candidates_export.py    # NEW
```

---

### Task 1: `build_port_candidates` export

**Files:**
- Modify: `mai/src/mai/publish/dataviz.py`
- Create: `mai/tests/test_port_candidates_export.py`

**Interfaces:**
- Produces: `build_port_candidates(session) -> dict` (schema below); a `port_candidates.json` write in `write_dataviz`.

- [ ] **Step 1: Write the failing test**

`mai/tests/test_port_candidates_export.py`:

```python
from mai.db.models import (Commit, PatchGroup, PortCandidate, Repo)
from mai.publish.dataviz import build_port_candidates


async def _pg(session, patch_id):
    pg = PatchGroup(patch_id=patch_id)
    session.add(pg)
    await session.flush()
    return pg


async def _commit(session, core, sha, message):
    c = Commit(core=core, sha=sha, author="a", authored_at="t", committer="a",
               committed_at="t", message=message, parent_shas=["p"], is_merge=False)
    session.add(c)
    await session.flush()


async def test_groups_by_target_sorts_and_joins(session):
    session.add(Repo(full_name="r-log/server-two", core="two",
                     url="https://github.com/r-log/server-two"))
    await _commit(session, "two", "ABC123", "Fix realm auth length\n\nbody")
    pg1 = await _pg(session, "patchidA1")
    pg2 = await _pg(session, "patchidB2")
    # a small (mag 200) and a surgical (mag 4) candidate, both target three from two
    session.add(PortCandidate(patch_group_id=pg1.id, source_core="two", target_core="three",
                              subsystem="src/shared/Auth", classification="shared", magnitude=200,
                              tier="small", confidence="high", evidence=["e1"], status="open",
                              source_sha="ABC123"))
    session.add(PortCandidate(patch_group_id=pg2.id, source_core="two", target_core="three",
                              subsystem="src/shared/Auth", classification="shared", magnitude=4,
                              tier="surgical", confidence="high", evidence=["e2"], status="open",
                              source_sha="ABC123"))
    # an excluded (dismissed) candidate
    session.add(PortCandidate(patch_group_id=pg1.id, source_core="two", target_core="one",
                              subsystem="src/shared/Auth", classification="shared", magnitude=4,
                              tier="surgical", confidence="high", evidence=[], status="dismissed",
                              source_sha="ABC123"))
    await session.commit()

    out = await build_port_candidates(session)
    assert out["summary"]["total"] == 2          # only open
    assert out["summary"]["tiers"] == {"surgical": 1, "small": 1, "moderate": 0, "bulk": 0}
    cols = {c["core"]: c for c in out["columns"]}
    assert [c["core"] for c in out["columns"]] == ["zero", "one", "two", "three"]  # all cores, ordered
    three = cols["three"]
    assert three["count"] == 2
    assert [x["tier"] for x in three["candidates"]] == ["surgical", "small"]  # surgical first
    card = three["candidates"][0]
    assert card["id"] == f"{pg2.id}:three"
    assert card["title"] == "Fix realm auth length"
    assert card["source_core"] == "two"
    assert card["source_url"] == "https://github.com/r-log/server-two/commit/ABC123"
    assert card["patch_id"] == "patchidB2"
    assert cols["zero"]["count"] == 0            # empty fork still gets a column


async def test_title_and_url_fallbacks(session):
    pg = await _pg(session, "p")
    session.add(PortCandidate(patch_group_id=pg.id, source_core="two", target_core="three",
                              subsystem="src/shared/Log", classification="shared", magnitude=2,
                              tier="surgical", confidence="high", evidence=[], status="open",
                              source_sha="NOSHA"))  # no Commit, no Repo
    await session.commit()
    out = await build_port_candidates(session)
    card = {c["core"]: c for c in out["columns"]}["three"]["candidates"][0]
    assert card["title"] == "src/shared/Log fix (NOSHA)"  # fallback title
    assert card["source_url"] is None                      # no repo -> no link
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/Users/Roko/Documents/PYTHON/MANGOS/mai && python -m pytest tests/test_port_candidates_export.py -v`
Expected: FAIL with `ImportError: cannot import name 'build_port_candidates'`
(Use `py -3.12 -m pytest ...` if `python` is not 3.12.)

- [ ] **Step 3: Add `build_port_candidates` + helper to `dataviz.py`**

In `mai/src/mai/publish/dataviz.py`, extend the model import line to include the new models — change:

```python
from mai.db.models import DriftObservation, Report, SourceRecord, Verification
```

to:

```python
from mai.db.models import (Commit, DriftObservation, PatchGroup, PortCandidate,
                           Report, Repo, SourceRecord, Verification)
```

Then add these near the other builders (e.g. after `build_pushes`):

```python
_TIER_RANK = {"surgical": 0, "small": 1, "moderate": 2, "bulk": 3}
_CORE_ORDER = {"zero": 0, "one": 1, "two": 2, "three": 3}


async def _source_repos(session: AsyncSession) -> dict[str, str]:
    """core -> repo full_name for building commit URLs (prefer the .../server repo)."""
    repos: dict[str, str] = {}
    for r in await session.scalars(select(Repo)):
        if r.core not in repos or r.full_name.endswith("/server"):
            repos[r.core] = r.full_name
    return repos


async def build_port_candidates(session: AsyncSession) -> dict:
    """Open port candidates grouped by target fork, quick-wins first, for /port/."""
    repos = await _source_repos(session)
    pg_patch = {pg.id: pg.patch_id for pg in await session.scalars(select(PatchGroup))}
    cands = list(await session.scalars(
        select(PortCandidate).where(PortCandidate.status == "open")))

    tiers = {"surgical": 0, "small": 0, "moderate": 0, "bulk": 0}
    by_target: dict[str, list] = {}
    for pc in cands:
        commit = await session.scalar(
            select(Commit).where(Commit.core == pc.source_core,
                                 Commit.sha == pc.source_sha))
        title = commit.message.strip().splitlines()[0] if commit and commit.message else ""
        if not title:
            title = f"{pc.subsystem} fix ({(pc.source_sha or '')[:8]})"
        repo = repos.get(pc.source_core)
        source_url = (f"https://github.com/{repo}/commit/{pc.source_sha}"
                      if repo and pc.source_sha else None)
        by_target.setdefault(pc.target_core, []).append({
            "id": f"{pc.patch_group_id}:{pc.target_core}",
            "title": title,
            "source_core": pc.source_core,
            "source_url": source_url,
            "subsystem": pc.subsystem,
            "tier": pc.tier,
            "magnitude": pc.magnitude,
            "confidence": pc.confidence,
            "patch_id": (pg_patch.get(pc.patch_group_id) or "")[:12],
            "evidence": pc.evidence,
        })
        if pc.tier in tiers:
            tiers[pc.tier] += 1

    all_targets = sorted(set(by_target) | set(_CORE_ORDER),
                         key=lambda c: (_CORE_ORDER.get(c, 99), c))
    columns = []
    for core in all_targets:
        items = by_target.get(core, [])
        items.sort(key=lambda x: (_TIER_RANK.get(x["tier"], 9), x["magnitude"]))
        columns.append({"core": core, "repo": repos.get(core, ""),
                        "count": len(items), "candidates": items})
    return {"summary": {"total": len(cands), "tiers": tiers}, "columns": columns}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /c/Users/Roko/Documents/PYTHON/MANGOS/mai && python -m pytest tests/test_port_candidates_export.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Wire `port_candidates.json` into `write_dataviz`**

In `mai/src/mai/publish/dataviz.py`, append to `write_dataviz` (after the `pushes.json` write):

```python
    (data / "port_candidates.json").write_text(
        json.dumps(await build_port_candidates(session), indent=2), encoding="utf-8")
```

- [ ] **Step 6: Run the full suite (no regression)**

Run: `cd /c/Users/Roko/Documents/PYTHON/MANGOS/mai && python -m pytest -q`
Expected: PASS (prior 173 + 2 new = 175 passed).

- [ ] **Step 7: Commit**

```bash
git add mai/src/mai/publish/dataviz.py mai/tests/test_port_candidates_export.py
git commit -m "feat: export open port candidates to port_candidates.json (grouped by target fork)"
```

---

### Task 2: `/port/` page + column/card render

**Files:**
- Create: `mai/mai-data/content/port/_index.md`
- Create: `mai/mai-data/layouts/port/list.html`
- Create: `mai/mai-data/static/js/portboard.js`
- Modify: `mai/mai-data/static/css/mai.css`

**Interfaces:**
- Consumes: `port_candidates.json` (Task 1) via `window.MAI_PORT`.
- Produces: a `/port/` page rendering four target-fork columns of cards (render-only this task; triage/filters in Task 3).

- [ ] **Step 1: Create the section content page**

`mai/mai-data/content/port/_index.md`:

```markdown
---
title: "Port Debt"
---
```

- [ ] **Step 2: Create the page layout**

`mai/mai-data/layouts/port/list.html`:

```html
{{ define "main" }}
  <header class="port-head">
    <h1>Port Debt</h1>
    <div id="port-summary" class="port-summary muted"></div>
  </header>
  <div class="port-filters" id="port-filters" hidden>
    <select id="f-tier"><option value="">all tiers</option><option>surgical</option><option>small</option><option>moderate</option><option>bulk</option></select>
    <select id="f-source"><option value="">any source</option></select>
    <input id="f-search" type="search" placeholder="search title / subsystem">
    <label class="showdis"><input type="checkbox" id="f-dismissed"> show dismissed</label>
  </div>
  <div class="port-board" id="port-board"><div class="empty-state">No port-debt data yet.</div></div>
  <script>window.MAI_PORT = {{ .Site.Data.port_candidates | jsonify | safeJS }};</script>
  <script src="/js/portboard.js"></script>
{{ end }}
```

- [ ] **Step 3: Create the render-only `portboard.js`**

`mai/mai-data/static/js/portboard.js`:

```javascript
(function () {
  var P = window.MAI_PORT;
  var board = document.getElementById('port-board');
  var summary = document.getElementById('port-summary');
  if (!board || !P || typeof P !== 'object' || !P.columns) return;

  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  var TIER = { surgical: '#1a7f37', small: '#9a6700', moderate: '#bc4c00', bulk: '#cf222e' };

  if (summary && P.summary) {
    var t = P.summary.tiers || {};
    summary.textContent = (P.summary.total || 0) + ' open · surgical ' + (t.surgical || 0)
      + ' · small ' + (t.small || 0) + ' · moderate ' + (t.moderate || 0) + ' · bulk ' + (t.bulk || 0);
  }

  function cardHTML(c) {
    var dot = '<span class="tdot" style="background:' + (TIER[c.tier] || '#59636e') + '"></span>';
    var link = c.source_url ? '<a class="src-link" href="' + esc(c.source_url) + '" target="_blank" rel="noopener">↗</a>' : '';
    return '<article class="pcard" data-id="' + esc(c.id) + '" data-tier="' + esc(c.tier)
      + '" data-source="' + esc(c.source_core) + '" data-text="'
      + esc((c.title + ' ' + c.subsystem).toLowerCase()) + '">'
      + '<div class="pc-top">' + dot + '<span class="pc-from">from ' + esc(c.source_core) + '</span>' + link + '</div>'
      + '<div class="pc-title">' + esc(c.title) + '</div>'
      + '<div class="pc-meta">' + esc(c.subsystem) + ' · ' + esc(c.magnitude) + ' lines</div>'
      + '<ul class="pc-evidence" hidden>' + (c.evidence || []).map(function (e) {
          return '<li>' + esc(e) + '</li>'; }).join('') + '</ul>'
      + '</article>';
  }

  board.innerHTML = P.columns.map(function (col) {
    var cards = col.candidates.length
      ? col.candidates.map(cardHTML).join('')
      : '<div class="empty-state">nothing to port in</div>';
    return '<section class="pcol" data-core="' + esc(col.core) + '">'
      + '<div class="pcol-h"><span class="pcol-name">Port into ' + esc(col.core.toUpperCase())
      + '</span><span class="pcol-ct">' + col.count + '</span></div>'
      + '<div class="pcol-cards">' + cards + '</div></section>';
  }).join('');
})();
```

- [ ] **Step 4: Add the `.port-*` styles to `mai.css`**

Append to `mai/mai-data/static/css/mai.css`:

```css
.port-head{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin:8px 0 4px}
.port-summary{font-size:13px}
.port-filters{display:flex;gap:8px;align-items:center;margin:10px 0;flex-wrap:wrap}
.port-filters select,.port-filters input{padding:4px 8px;border:1px solid #d0d7de;border-radius:6px;font-size:13px}
.port-board{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;align-items:start}
.pcol{background:#f6f8fa;border:1px solid #d0d7de;border-radius:8px;min-height:80px}
.pcol-h{display:flex;justify-content:space-between;align-items:center;padding:8px 10px;border-bottom:1px solid #d0d7de;font-weight:600}
.pcol-ct{background:#eaeef2;border-radius:20px;padding:0 8px;font-size:12px;font-weight:600}
.pcol-cards{padding:8px;display:flex;flex-direction:column;gap:8px}
.pcard{background:#fff;border:1px solid #d0d7de;border-radius:6px;padding:8px;cursor:pointer}
.pc-top{display:flex;align-items:center;gap:6px;font-size:12px;color:#59636e}
.tdot{width:9px;height:9px;border-radius:50%;display:inline-block}
.pc-from{flex:1}
.src-link{text-decoration:none;font-size:14px}
.pc-title{font-weight:600;font-size:13px;margin:3px 0}
.pc-meta{font-size:12px;color:#59636e}
.pc-evidence{margin:6px 0 0;padding-left:16px;font-size:11px;color:#59636e}
.pcard.working{border-color:#0969da;box-shadow:inset 3px 0 0 #0969da}
.pcard.done{opacity:.55}
.pcard.done .pc-title{text-decoration:line-through}
.pcard.dismissed{display:none}
.port-board.show-dismissed .pcard.dismissed{display:block;opacity:.4}
.pc-actions{display:flex;gap:6px;margin-top:6px}
.pc-actions button{font-size:11px;padding:2px 6px;border:1px solid #d0d7de;border-radius:5px;background:#fff;cursor:pointer}
.pc-actions button.on{background:#0969da;color:#fff;border-color:#0969da}
```

- [ ] **Step 5: Generate data + build the site**

Run:
```bash
cd /c/Users/Roko/Documents/PYTHON/MANGOS/mai && rm -f /c/tmp/mai_real_run.db && python mai-data/tmp/real_run.py >/dev/null 2>&1; \
DATABASE_URL="sqlite+aiosqlite:///C:/tmp/mai_real_run.db" python -m mai.cli.__main__ publish >/dev/null 2>&1; \
ls -la mai-data/data/port_candidates.json && hugo -s mai-data 2>&1 | tail -3
```
Expected: `port_candidates.json` exists and is non-trivial; `hugo` build reports `Pages` built with no error. (If the `real_run.py` mirrors are already cached this is fast. If `publish` requires the data already present, the prior `real_run.py` left `mai-data/data/port_candidates.json` via `write_dataviz` — confirm the file exists; if not, run `python -m mai.cli.__main__ publish` against the populated DB.)

- [ ] **Step 6: Eyeball the page**

Run: `cd /c/Users/Roko/Documents/PYTHON/MANGOS/mai && hugo server -s mai-data` (then open `/port/`), OR inspect `mai-data/public/port/index.html` for the four `pcol` columns + cards. Expected: four "Port into …" columns; populated columns show tier-dot cards with titles; empty forks show "nothing to port in". Stop the server.

- [ ] **Step 7: Commit**

```bash
git add mai/mai-data/content/port/_index.md mai/mai-data/layouts/port/list.html mai/mai-data/static/js/portboard.js mai/mai-data/static/css/mai.css
git commit -m "feat: /port/ page rendering port-debt columns and cards"
```

---

### Task 3: triage overlay + filters + evidence expand

**Files:**
- Modify: `mai/mai-data/static/js/portboard.js` (replace with the full interactive version)

**Interfaces:**
- Consumes: the render structure from Task 2.
- Produces: `localStorage["mai.portdebt"]` triage map; tier/source/search filters; click-to-expand evidence.

- [ ] **Step 1: Replace `portboard.js` with the interactive version**

Replace the entire contents of `mai/mai-data/static/js/portboard.js` with:

```javascript
(function () {
  var P = window.MAI_PORT;
  var board = document.getElementById('port-board');
  var summary = document.getElementById('port-summary');
  if (!board || !P || typeof P !== 'object' || !P.columns) return;

  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  var TIER = { surgical: '#1a7f37', small: '#9a6700', moderate: '#bc4c00', bulk: '#cf222e' };
  var KEY = 'mai.portdebt';

  // --- personal triage overlay (localStorage), pruned to current ids ---
  var ids = {};
  P.columns.forEach(function (col) { col.candidates.forEach(function (c) { ids[c.id] = 1; }); });
  function load() { try { return JSON.parse(localStorage.getItem(KEY) || '{}'); } catch (e) { return {}; } }
  function save() { try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) {} }
  var state = load();
  state._v = 1;
  Object.keys(state).forEach(function (k) { if (k !== '_v' && !ids[k]) delete state[k]; });
  save();

  if (summary && P.summary) {
    var t = P.summary.tiers || {};
    summary.textContent = (P.summary.total || 0) + ' open · surgical ' + (t.surgical || 0)
      + ' · small ' + (t.small || 0) + ' · moderate ' + (t.moderate || 0) + ' · bulk ' + (t.bulk || 0);
  }

  function cardHTML(c) {
    var dot = '<span class="tdot" style="background:' + (TIER[c.tier] || '#59636e') + '"></span>';
    var link = c.source_url ? '<a class="src-link" href="' + esc(c.source_url) + '" target="_blank" rel="noopener">↗</a>' : '';
    var st = state[c.id] || '';
    return '<article class="pcard ' + esc(st) + '" data-id="' + esc(c.id) + '" data-tier="' + esc(c.tier)
      + '" data-source="' + esc(c.source_core) + '" data-text="'
      + esc((c.title + ' ' + c.subsystem).toLowerCase()) + '">'
      + '<div class="pc-top">' + dot + '<span class="pc-from">from ' + esc(c.source_core) + '</span>' + link + '</div>'
      + '<div class="pc-title">' + esc(c.title) + '</div>'
      + '<div class="pc-meta">' + esc(c.subsystem) + ' · ' + esc(c.magnitude) + ' lines</div>'
      + '<ul class="pc-evidence" hidden>' + (c.evidence || []).map(function (e) {
          return '<li>' + esc(e) + '</li>'; }).join('') + '</ul>'
      + '<div class="pc-actions">'
      + '<button data-act="working" class="' + (st === 'working' ? 'on' : '') + '">working</button>'
      + '<button data-act="done" class="' + (st === 'done' ? 'on' : '') + '">done</button>'
      + '<button data-act="dismissed" class="' + (st === 'dismissed' ? 'on' : '') + '">✕</button>'
      + '</div></article>';
  }

  board.innerHTML = P.columns.map(function (col) {
    var cards = col.candidates.length
      ? col.candidates.map(cardHTML).join('')
      : '<div class="empty-state">nothing to port in</div>';
    return '<section class="pcol" data-core="' + esc(col.core) + '">'
      + '<div class="pcol-h"><span class="pcol-name">Port into ' + esc(col.core.toUpperCase())
      + '</span><span class="pcol-ct">' + col.count + '</span></div>'
      + '<div class="pcol-cards">' + cards + '</div></section>';
  }).join('');

  // --- interactions: evidence expand + triage actions ---
  board.addEventListener('click', function (e) {
    var btn = e.target.closest('.pc-actions button');
    var card = e.target.closest('.pcard');
    if (!card) return;
    if (btn) {
      e.stopPropagation();
      var id = card.getAttribute('data-id'), act = btn.getAttribute('data-act');
      state[id] = (state[id] === act) ? undefined : act;  // toggle off if same
      if (!state[id]) delete state[id];
      save();
      card.className = 'pcard ' + (state[id] || '');
      Array.prototype.forEach.call(card.querySelectorAll('.pc-actions button'), function (b) {
        b.classList.toggle('on', b.getAttribute('data-act') === state[id]);
      });
      return;
    }
    var ev = card.querySelector('.pc-evidence');
    if (ev) ev.hidden = !ev.hidden;
  });

  // --- filters ---
  var fTier = document.getElementById('f-tier'), fSrc = document.getElementById('f-source');
  var fSearch = document.getElementById('f-search'), fDis = document.getElementById('f-dismissed');
  var filters = document.getElementById('port-filters');
  if (filters) filters.hidden = false;
  // populate source options from data
  var sources = {};
  P.columns.forEach(function (col) { col.candidates.forEach(function (c) { sources[c.source_core] = 1; }); });
  if (fSrc) Object.keys(sources).sort().forEach(function (s) {
    var o = document.createElement('option'); o.textContent = s; fSrc.appendChild(o); });

  function applyFilters() {
    var tier = fTier ? fTier.value : '', src = fSrc ? fSrc.value : '';
    var q = fSearch ? fSearch.value.trim().toLowerCase() : '';
    board.classList.toggle('show-dismissed', !!(fDis && fDis.checked));
    Array.prototype.forEach.call(board.querySelectorAll('.pcard'), function (card) {
      var ok = (!tier || card.getAttribute('data-tier') === tier)
        && (!src || card.getAttribute('data-source') === src)
        && (!q || card.getAttribute('data-text').indexOf(q) !== -1);
      card.style.display = ok ? '' : 'none';
    });
  }
  [fTier, fSrc, fDis].forEach(function (el) { if (el) el.addEventListener('change', applyFilters); });
  if (fSearch) fSearch.addEventListener('input', applyFilters);
})();
```

- [ ] **Step 2: Rebuild and verify interactions**

Run: `cd /c/Users/Roko/Documents/PYTHON/MANGOS/mai && hugo -s mai-data 2>&1 | tail -2`
Expected: build succeeds. Then `hugo server -s mai-data`, open `/port/`, and confirm: clicking a card expands its evidence; the `working`/`done`/`✕` buttons restyle the card and survive a reload (localStorage); the tier/source/search filters hide non-matching cards; "show dismissed" toggles dismissed cards. Stop the server.

- [ ] **Step 3: Commit**

```bash
git add mai/mai-data/static/js/portboard.js
git commit -m "feat: port board triage overlay (localStorage), filters, and evidence expand"
```

---

### Task 4: nav link + full verify

**Files:**
- Modify: the nav markup (locate it — see Step 1)

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Add a "Port" link to the top nav**

Find the nav markup (the bar with Overview/Bugs/Drift links). Run:
```bash
cd /c/Users/Roko/Documents/PYTHON/MANGOS/mai && grep -rln "/sync/\|Drift\|Overview" mai-data/layouts/
```
In the file that holds the top-nav `<a>` links (a partial or `baseof.html`), add a sibling link to `/port/` next to the Drift link, matching the existing markup exactly, e.g.:

```html
<a href="/port/">Port</a>
```
(Match the surrounding link element/classes — copy the neighboring `<a>`'s attributes.)

- [ ] **Step 2: Full test suite**

Run: `cd /c/Users/Roko/Documents/PYTHON/MANGOS/mai && python -m pytest -q`
Expected: 175 passed (unchanged from Task 1; this task is template-only).

- [ ] **Step 3: Full publish + build on real data**

Run:
```bash
cd /c/Users/Roko/Documents/PYTHON/MANGOS/mai && rm -f /c/tmp/mai_real_run.db && python mai-data/tmp/real_run.py >/dev/null 2>&1 && \
DATABASE_URL="sqlite+aiosqlite:///C:/tmp/mai_real_run.db" python -m mai.cli.__main__ publish >/tmp/pub.log 2>&1; tail -1 /tmp/pub.log; \
hugo -s mai-data 2>&1 | tail -2 && grep -c "pcol" mai-data/public/port/index.html
```
Expected: publish reports pages; `hugo` builds clean; `grep -c pcol` ≥ 4 (four columns present in the built page). Confirm the nav shows "Port".

- [ ] **Step 4: Commit**

```bash
git add mai/mai-data/layouts
git commit -m "feat: add Port nav link to reach the port-debt board"
```

---

## Self-Review

- **Spec coverage:** `build_port_candidates` export (§6/§7) ✓ Task 1; `/port/` page + columns/cards + title/source_url (§8) ✓ Task 2; filters + evidence + `localStorage` triage with prune (§4/§8/§D) ✓ Task 3; nav + verify ✓ Task 4. Empty-state/degradation (§11 cases 1,2,3,4,8) handled (empty columns, `typeof !== 'object'` guard, title/url fallbacks). Existing dashboard board untouched (Non-Goal) ✓ — only new files + additive dataviz/nav lines.
- **Invariants:** read-only (no DB/GitHub writes; `localStorage` only) ✓ · static/offline (Hugo + `jsonify|safeJS`, fail-soft guards) ✓ · engine-owns-truth (only `status=="open"` exported) ✓ · stable+sticky+self-pruning ids (`patch_group_id:target_core`, prune on load) ✓ · sort surgical→bulk then magnitude ✓.
- **Placeholder scan:** none — every step has runnable code/commands + expected output.
- **Type consistency:** `build_port_candidates(session) -> dict` keys (`summary{total,tiers}`, `columns[{core,repo,count,candidates[{id,title,source_core,source_url,subsystem,tier,magnitude,confidence,patch_id,evidence}]}]`) match the test assertions and the JS field reads (`P.summary.tiers`, `col.candidates`, `c.id/title/source_core/source_url/subsystem/tier/magnitude/evidence`). `localStorage` key `mai.portdebt` and states `working|done|dismissed` consistent across JS + spec. `window.MAI_PORT` embedded once (Task 2 list.html), read in both JS versions.

## Notes for later

- **Multi-user successor** (`port-debt-board-multiuser.md`): the `localStorage["mai.portdebt"]` map (id→state) is the seam — swap it for per-user server state behind auth; the JSON contract and card ids carry over unchanged.
- **Large columns:** if real backlogs make a column huge, add a per-column "show more" cap (default currently shows all, surgical-first).
- **Retire dashboard `pushes.json` board?** once `/port/` proves itself; kept for now ("what landed" vs "what's missing" are different questions).
- The `build_port_candidates` per-candidate `Commit` lookup is N+1 (fine for an offline publish step, like `build_pushes`); batch later if publish time grows.
