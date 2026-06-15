# Dashboard Workspace Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the Mai landing page into a workspace — a balanced overview whose hero is a fully-playable 3D drift view, followed by a per-core porting board with drag-to-TODO.

**Architecture:** Server side stays Python (SQLAlchemy async): `dataviz.py` gains coverage fields, raw-ratio drift heights, and a `pushes.json` export; the harvester already ingests merged PRs. Client side gains two vanilla-JS components — a rewritten `frequency3d.js` (Three.js + OrbitControls, client-side normalization) and a new `board.js` (HTML5 drag/drop + `localStorage`). Hugo wires JSON into the page via `.Site.Data | jsonify | safeJS`.

**Tech Stack:** Python 3.12, SQLAlchemy 2.0 async, pytest (aiosqlite), Hugo static site, Three.js r128 + OrbitControls (vendored), vanilla JS.

**Spec:** `docs/specs/dashboard-workspace-redesign.md`

---

## File Structure

**Modify (Python):**
- `src/mai/publish/dataviz.py` — add `coverage` to `build_dashboard`; change `build_frequency` to raw ratios + `null` gaps; add `build_pushes`; write `pushes.json` in `write_dataviz`.
- `tests/test_dataviz.py` — update the `build_frequency` ×1.5 assertion; add coverage/pushes tests.

**Modify (Hugo/JS):**
- `mai-data/layouts/_default/baseof.html` — top nav + search.
- `mai-data/layouts/index.html` — restructured dashboard (coverage strip, tiles, 3D hero, porting board, secondary panels).
- `mai-data/layouts/sync/list.html` — load vendored libs; mount the rewritten component.
- `mai-data/static/js/frequency3d.js` — rewritten playable component.
- `mai-data/static/css/mai.css` — v2 token/component additions.

**Create:**
- `mai-data/static/js/vendor/three.min.js`, `mai-data/static/js/vendor/OrbitControls.js` — vendored.
- `mai-data/static/js/board.js` — porting board.
- `mai-data/layouts/partials/freq3d.html` — the 3D hero fragment (shared by dashboard + drift page).

---

## Phase 1 — Dashboard shell (no new JS)

### Task 1: Coverage block in `build_dashboard`

**Files:**
- Modify: `src/mai/publish/dataviz.py` (`build_dashboard`)
- Test: `tests/test_dataviz.py`

- [ ] **Step 1: Write the failing test**

```python
async def test_build_dashboard_coverage(session):
    from mai.publish.dataviz import build_dashboard
    await ingest_event(session, IntakeEvent("ips", "r1", "Pet bug", "three",
                                            raw_payload={"markdown": "x"}))
    await ingest_event(session, IntakeEvent("ips", "r2", "Spell bug", "zero",
                                            raw_payload={"markdown": "y"}))
    await session.commit()
    dash = await build_dashboard(session)
    cov = dash["coverage"]
    assert cov["enriched"] == 0 and cov["total"] == 2
    assert {c["core"]: c["reports"] for c in cov["cores"]} == {"three": 1, "zero": 1}
    assert isinstance(cov["generated_at"], str) and "T" in cov["generated_at"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_dataviz.py::test_build_dashboard_coverage -v`
Expected: FAIL with `KeyError: 'coverage'`.

- [ ] **Step 3: Implement**

At the top of `src/mai/publish/dataviz.py` add imports (merge with existing):

```python
from datetime import datetime, timezone

from sqlalchemy import func
from mai.db.models import Report
```

In `build_dashboard`, before the final `return`, build the coverage block and add it to the returned dict:

```python
    per_core_rows = await session.execute(
        select(Report.core, func.count()).group_by(Report.core))
    per_core = sorted(
        ({"core": core, "reports": n} for core, n in per_core_rows),
        key=lambda c: c["reports"], reverse=True)
    coverage = {
        "total": stats["reports"],
        "enriched": stats["enriched"],
        "cores": per_core,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    return {"stats": stats, "top_areas": top_areas,
            "recently_fixed": fixed, "coverage": coverage}
```

(Delete the old `return {"stats": ...}` line it replaces.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_dataviz.py::test_build_dashboard_coverage -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mai/publish/dataviz.py tests/test_dataviz.py
git commit -m "feat: dashboard coverage block (per-core counts, enriched, freshness)"
```

### Task 2: CSS v2 additions

**Files:**
- Modify: `mai-data/static/css/mai.css` (append)

- [ ] **Step 1: Append the v2 components**

Append to `mai-data/static/css/mai.css`:

```css
/* ---- web v2: dashboard workspace ---- */
.topbar .search{margin-left:auto;width:220px;background:var(--canvas);
  border:1px solid var(--border);border-radius:6px;padding:5px 10px;color:var(--muted);font-size:13px}
.coverage{display:flex;align-items:center;gap:16px;flex-wrap:wrap;background:#fff;
  border:1px solid var(--border);border-radius:12px;padding:10px 16px;margin:18px 0 16px;font-size:13px}
.coverage .sep{width:1px;height:18px;background:var(--border)}
.corechip{font-size:12px;padding:2px 8px;border-radius:20px;font-weight:600;border:1px solid var(--border);
  background:var(--canvas);color:var(--muted)}
.corechip.on{background:#1a7f3714;color:var(--success,#1a7f37);border-color:#1a7f3733}
.meter{width:120px;height:8px;border-radius:6px;background:#eaeef2;overflow:hidden;display:inline-block;vertical-align:middle}
.meter>i{display:block;height:100%;background:linear-gradient(90deg,#2ea043,#0969da)}
.cov-fresh{margin-left:auto;color:var(--muted);font-size:12.5px}
.stats{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:18px}
.tile{background:#fff;border:1px solid var(--border);border-radius:12px;padding:13px 15px;position:relative}
.tile .n{font-size:24px;font-weight:700;letter-spacing:-.5px;line-height:1.1}
.tile .l{color:var(--muted);font-size:12.5px;margin-top:2px}
.tile .spark{position:absolute;right:10px;top:12px;font-size:11px;font-weight:600;color:var(--success,#1a7f37)}
.tile.accent{box-shadow:inset 3px 0 0 #0969da}.tile.green{box-shadow:inset 3px 0 0 #1a7f37}
.tile.amber{box-shadow:inset 3px 0 0 #9a6700}.tile.purple{box-shadow:inset 3px 0 0 #8250df}
.two{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:16px}

/* 3D hero */
.freq-hero{position:relative;background:#0b0f17;border:1px solid #10161f;border-radius:12px;overflow:hidden;margin:18px 0}
.freq-hero .hh{padding:12px 16px;border-bottom:1px solid #1d2633;display:flex;align-items:center;justify-content:space-between}
.freq-hero .hh h3{margin:0;font-size:13.5px;color:#e6edf3}
.freq-hero .hh .sub{color:#7d8da1;font-size:12px}
.freq-hero .hh a{color:#58a6ff;font-size:12.5px}
#freq-c{display:block;width:100%;height:420px;cursor:grab}#freq-c:active{cursor:grabbing}
.freq-err{color:#f85149;font-size:13px;padding:40px;text-align:center}
.freq-layers{position:absolute;top:60px;left:14px;background:rgba(13,18,26,.82);border:1px solid #232d3a;
  border-radius:10px;padding:9px;width:184px}
.freq-layers .lt{color:#8b97a6;font-size:10.5px;text-transform:uppercase;letter-spacing:.6px;margin:2px 4px 7px}
.lrow{display:flex;align-items:center;gap:8px;padding:5px 6px;border-radius:7px;cursor:pointer;user-select:none}
.lrow:hover{background:#1a2330}
.lrow .dot{width:11px;height:11px;border-radius:3px;flex:none}
.lrow .ln{font-size:12.5px;color:#dbe3ec;flex:1}.lrow .pct{font-size:11px;color:#7d8da1}
.lrow .eye{font-size:13px;color:#7d8da1;width:16px;text-align:center}
.lrow.off{opacity:.4}.lrow.off .eye{color:#4a5666}
.lrow .solo{font-size:10px;color:#58a6ff;opacity:0;margin-left:2px}.lrow:hover .solo{opacity:1}
.freq-layers .allbtn{margin-top:6px;width:100%;background:#172030;border:1px solid #28344a;color:#9fb3c9;
  font-size:11.5px;border-radius:7px;padding:5px;cursor:pointer}
.freq-ctrls{display:flex;gap:16px;align-items:center;flex-wrap:wrap;padding:9px 16px;color:#7d8da1;font-size:12px;
  border-top:1px solid #1d2633}
.freq-ctrls select,.freq-ctrls .fbtn{background:#161c26;border:1px solid #28344a;color:#cdd9e5;border-radius:6px;
  padding:4px 9px;font-size:12px;cursor:pointer}
.freq-ctrls input[type=range]{accent-color:#58a6ff;width:90px}
.freq-legend{display:flex;align-items:center;gap:8px;color:#7d8da1;font-size:11.5px;padding:8px 16px}
.hm-grad{width:120px;height:8px;border-radius:6px;background:linear-gradient(90deg,#2ea043,#d29922,#f85149)}

/* porting board */
.board-head{display:flex;align-items:center;justify-content:space-between;margin:6px 0 10px}
.board-head h2{font-size:16px;margin:0}.board-head .hint{color:var(--muted);font-size:12.5px}
.board{display:grid;grid-template-columns:repeat(4,1fr) 1.1fr;gap:12px;align-items:start}
.col{background:#fff;border:1px solid var(--border);border-radius:12px;min-height:200px;display:flex;flex-direction:column}
.col .colh{padding:10px 12px;border-bottom:1px solid var(--canvas);display:flex;align-items:center;gap:8px}
.col .colh .cname{font-weight:700;font-size:13.5px}.col .colh .ct{margin-left:auto;color:var(--muted);font-size:11.5px}
.col .cards{padding:10px;display:flex;flex-direction:column;gap:8px;flex:1;min-height:50px}
.col.todo{background:#fff8ef;border-color:#e6d4b3}.col.todo .colh{background:#fcefd7;border-bottom-color:#e6d4b3}
.bcard{background:var(--canvas);border:1px solid var(--border);border-radius:9px;padding:9px 10px;cursor:grab}
.bcard:active{cursor:grabbing}.bcard.dragging{opacity:.45}
.bcard .ct{font-size:12.5px;font-weight:600;line-height:1.35;margin-bottom:6px}
.bcard .cm{display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.bpill{font-size:10.5px;padding:1px 7px;border-radius:20px;font-weight:600}
.bpr{font-size:11px;color:var(--muted)}
.cards.over{outline:2px dashed #0969da;outline-offset:-4px;border-radius:9px;background:#0969da08}
.col.todo .cards.over{outline-color:#9a6700;background:#9a670010}
.tcard{position:relative}.tcard.done .ct{text-decoration:line-through;opacity:.6}
.tcard .src{font-size:10.5px;color:var(--muted);margin-top:5px;display:flex;align-items:center;gap:5px}
.tcard .arrow{color:#9a6700;font-weight:700}
.tcard .x{position:absolute;right:7px;top:7px;color:var(--muted);cursor:pointer;font-size:13px;line-height:1}
.tcard .x:hover{color:#cf222e}
.tcard .target{font-size:10.5px;border:1px solid var(--border);border-radius:6px;background:#fff;padding:1px 4px}
.tcard .donebox{margin-left:auto}
.empty-state{color:var(--muted);font-size:12px;font-style:italic;text-align:center;padding:20px 8px}
.board-foot{color:var(--muted);font-size:12px;margin-top:8px}
```

- [ ] **Step 2: Verify the stylesheet still builds**

Run: `cd mai-data && hugo --quiet --destination /tmp/h1 && echo OK`
Expected: `OK` (no template/asset errors).

- [ ] **Step 3: Commit**

```bash
git add mai-data/static/css/mai.css
git commit -m "feat: web v2 css (coverage strip, tiles, 3D hero, porting board)"
```

### Task 3: Top nav + search in `baseof.html`

**Files:**
- Modify: `mai-data/layouts/_default/baseof.html`

- [ ] **Step 1: Replace the `<header>` block**

Replace lines 10–13 (the `<header class="topbar">…</header>`) with:

```html
  <header class="topbar">
    <a class="brand" href="/">🐛 Mai</a>
    <nav>
      <a href="/">Overview</a>
      <a href="/sync/">Drift</a>
    </nav>
    <div class="search">Search bugs, areas, cores…</div>
  </header>
```

- [ ] **Step 2: Verify build**

Run: `cd mai-data && hugo --quiet --destination /tmp/h2 && grep -q "Overview" /tmp/h2/index.html && echo OK`
Expected: `OK`.

- [ ] **Step 3: Commit**

```bash
git add mai-data/layouts/_default/baseof.html
git commit -m "feat: top nav with search affordance"
```

### Task 4: Restructured dashboard (`index.html`) — coverage + tiles + secondary panels

This keeps the existing flat-heatmap hero *temporarily*; Phase 2 swaps it for the 3D partial.

**Files:**
- Modify: `mai-data/layouts/index.html`

- [ ] **Step 1: Replace the file contents**

```html
{{ define "main" }}
  {{ $d := .Site.Data.dashboard }}
  {{ $drift := .Site.Data.drift }}
  <h1>getMaNGOS Bug &amp; Drift Observatory</h1>
  <p class="muted">Verified bug status, cross-core divergence, and a porting workspace.</p>

  {{ with $d.coverage }}
  <div class="coverage">
    <b>Cores</b>
    {{ range .cores }}<span class="corechip on">{{ .core }} <span class="muted">{{ .reports }}</span></span>{{ end }}
    <div class="sep"></div>
    <b>Enriched</b> {{ .enriched }}/{{ .total }}
    <span class="meter"><i style="width:{{ if .total }}{{ math.Round (mul (div (float .enriched) .total) 100) }}{{ else }}0{{ end }}%"></i></span>
    <div class="cov-fresh">⟳ {{ .generated_at }}</div>
  </div>
  {{ end }}

  {{ with $d.stats }}
  <div class="stats">
    <div class="tile accent"><div class="n">{{ .reports }}</div><div class="l">Reports</div></div>
    <div class="tile green"><div class="n">{{ .fixed_confirmed }}</div><div class="l">Confirmed fixed</div>
      {{ if .fixed_confirmed }}<span class="spark">▲ ready to close</span>{{ end }}</div>
    <div class="tile amber"><div class="n">{{ .likely_fixed }}</div><div class="l">Likely fixed</div></div>
    <div class="tile"><div class="n">{{ .open }}</div><div class="l">Open</div></div>
    <div class="tile purple"><div class="n">{{ .drift_pairs }}</div><div class="l">Drift pairs</div></div>
  </div>
  {{ end }}

  {{/* HERO PLACEHOLDER — replaced by the 3D partial in Phase 2 */}}
  <div class="panel">
    <div class="panel-h">Cross-core drift — % of shared files diverged</div>
    <div class="panel-b">
      {{ with $drift }}
      <table class="hm">
        <tr><th></th>{{ range .cores }}<th>{{ . }}</th>{{ end }}</tr>
        {{ range .rows }}<tr><td class="hm-l">{{ .core }}</td>
          {{ range .cells }}{{ if .self }}<td class="hm-c na">—</td>
            {{ else if ne .value nil }}<td class="hm-c" style="background:{{ .color }}">{{ .value }}</td>
            {{ else }}<td class="hm-c na"></td>{{ end }}{{ end }}</tr>{{ end }}
      </table>
      {{ else }}<p class="muted">No drift data yet — run <code>mai drift</code>.</p>{{ end }}
    </div>
  </div>

  <div class="two">
    <div class="panel">
      <div class="panel-h">Open bugs by area</div>
      <div class="panel-b">
        {{ range $d.top_areas }}
          <div class="brow">
            <span class="pill" style="background:{{ .color }}1f;color:{{ .color }};border-color:{{ .color }}3a">{{ .name }}</span>
            <span class="bar" style="width:{{ mul .count 10 }}px;background:{{ .color }}"></span>
            <span class="muted">{{ .count }}</span>
          </div>
        {{ end }}
      </div>
    </div>
    <div class="panel">
      <div class="panel-h">Recently confirmed fixed</div>
      <div class="panel-b">
        {{ range $d.recently_fixed }}
          <div class="rfix">&#10004; <a href="{{ .url }}">{{ .title }}</a>
            <span class="muted">— {{ .id }} → {{ .related }}</span></div>
        {{ else }}<p class="muted">No confirmed fixes yet.</p>{{ end }}
      </div>
    </div>
  </div>
{{ end }}
```

- [ ] **Step 2: Verify build with real data present**

Run: `cd mai-data && hugo --quiet --destination /tmp/h3 && grep -q "Confirmed fixed" /tmp/h3/index.html && grep -q "Enriched" /tmp/h3/index.html && echo OK`
Expected: `OK`. (Requires `data/dashboard.json` with a `coverage` block — regenerate it first if missing: `python -m mai.cli publish` then re-run, or run against a populated db.)

- [ ] **Step 3: Commit**

```bash
git add mai-data/layouts/index.html
git commit -m "feat: restructured dashboard (coverage strip, stat tiles, secondary panels)"
```

---

## Phase 2 — Playable 3D

### Task 5: `build_frequency` emits raw ratios + null gaps

**Files:**
- Modify: `src/mai/publish/dataviz.py` (`build_frequency`)
- Test: `tests/test_dataviz.py`

- [ ] **Step 1: Update the existing assertion + add a gap test**

In `tests/test_dataviz.py`, change the assertion in `test_build_frequency_heightfield` from:

```python
    assert f["intensity"][zero_full]["src/game/Object"] == 1.125   # 60/80 * 1.5
```
to:
```python
    assert f["intensity"][zero_full]["src/game/Object"] == 0.75    # raw 60/80, no scaling
```

Add a new test:

```python
async def test_build_frequency_raw_ratio_range(session):
    from mai.publish.dataviz import build_frequency
    d = DriftRepository(session)
    await d.upsert("mangoszero/server", "mangostwo/server", "src/game/Object",
                   {"shared": 80, "diverged": 60, "identical": 20, "only_a": 0, "only_b": 0})
    await session.commit()
    f = await build_frequency(session)
    for fork, subs in f["intensity"].items():
        for sub, v in subs.items():
            assert v is None or 0.0 <= v <= 1.0
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_dataviz.py::test_build_frequency_heightfield -v`
Expected: FAIL (`1.125 != 0.75`).

- [ ] **Step 3: Implement**

In `build_frequency`, replace the per-fork intensity loop so it emits the raw ratio and `None` where a subsystem is not shared:

```python
    intensity: dict[str, dict] = {}
    for fork in forks:
        per_sub = {}
        for sub in subsystems:
            vals = [o.diverged / o.shared for o in obs
                    if o.subsystem == sub["full"] and o.shared
                    and fork in (o.fork_a, o.fork_b)]
            per_sub[sub["full"]] = round(sum(vals) / len(vals), 3) if vals else None
        intensity[fork] = per_sub
```

(The `max` key may stay as-is; the client computes its own min/max.)

- [ ] **Step 4: Run tests to verify pass**

Run: `python -m pytest tests/test_dataviz.py -k frequency -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/mai/publish/dataviz.py tests/test_dataviz.py
git commit -m "feat: frequency.json emits raw diverged/shared ratios (normalize client-side)"
```

### Task 6: Vendor Three.js + OrbitControls

**Files:**
- Create: `mai-data/static/js/vendor/three.min.js`, `mai-data/static/js/vendor/OrbitControls.js`

- [ ] **Step 1: Download both into the vendor dir**

```bash
mkdir -p mai-data/static/js/vendor
curl -fsSL https://cdn.jsdelivr.net/npm/three@0.128.0/build/three.min.js \
  -o mai-data/static/js/vendor/three.min.js
curl -fsSL https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/controls/OrbitControls.js \
  -o mai-data/static/js/vendor/OrbitControls.js
```

- [ ] **Step 2: Verify they are non-empty and the right globals**

Run: `grep -q "THREE" mai-data/static/js/vendor/three.min.js && grep -q "THREE.OrbitControls" mai-data/static/js/vendor/OrbitControls.js && wc -c mai-data/static/js/vendor/*.js`
Expected: both grep succeed; `three.min.js` ~600 KB, `OrbitControls.js` ~50 KB.

- [ ] **Step 3: Commit**

```bash
git add mai-data/static/js/vendor/three.min.js mai-data/static/js/vendor/OrbitControls.js
git commit -m "chore: vendor three.js r128 + OrbitControls (offline/Access reliability)"
```

### Task 7: Rewrite `frequency3d.js` as the playable component

**Files:**
- Modify (overwrite): `mai-data/static/js/frequency3d.js`

- [ ] **Step 1: Replace the whole file**

```javascript
(function () {
  var F = window.MAI_FREQ;
  var err = document.getElementById('freq-err');
  function fail(msg) { if (err) err.textContent = msg; }
  if (!window.THREE) { return fail('3D library failed to load.'); }
  if (!F || !F.cores || !F.cores.length) { return fail('No drift data yet — run `mai drift`.'); }
  var canvas = document.getElementById('freq-c');
  var wrap = document.getElementById('freq');
  if (!canvas || !wrap) return;

  // ---- data → raw ratio lookup ----
  var CORES = F.cores;                       // [{name, full, y}]
  var SUBS = F.subsystems;                   // [{name, full, x, z}]
  function raw(coreFull, subFull) {
    var m = F.intensity[coreFull] || {};
    var v = m[subFull];
    return (v === null || v === undefined) ? null : v;
  }
  var fillMean = {};                         // per-core mean for null gaps
  CORES.forEach(function (c) {
    var vs = SUBS.map(function (s) { return raw(c.full, s.full); }).filter(function (v) { return v !== null; });
    fillMean[c.full] = vs.length ? vs.reduce(function (a, b) { return a + b; }, 0) / vs.length : 0.5;
  });
  var all = [];
  CORES.forEach(function (c) { SUBS.forEach(function (s) { var v = raw(c.full, s.full); if (v !== null) all.push(v); }); });
  var gMin = all.length ? Math.min.apply(null, all) : 0, gMax = all.length ? Math.max.apply(null, all) : 1;
  var subStat = {};
  SUBS.forEach(function (s) {
    var vs = CORES.map(function (c) { return raw(c.full, s.full); }).filter(function (v) { return v !== null; });
    var m = vs.length ? vs.reduce(function (a, b) { return a + b; }, 0) / vs.length : 0.5;
    var sd = vs.length ? Math.sqrt(vs.reduce(function (a, b) { return a + (b - m) * (b - m); }, 0) / vs.length) : 0.001;
    subStat[s.full] = { m: m, sd: sd || 0.001 };
  });

  var MODE = 'contrast', AMP = 1.8, GAP = 1.4;
  function height(coreFull, subFull) {
    var r = raw(coreFull, subFull); if (r === null) r = fillMean[coreFull];
    if (MODE === 'absolute') return (r - 0.55) / 0.45;
    if (MODE === 'contrast') return (gMax > gMin) ? (r - gMin) / (gMax - gMin) : 0.5;
    var st = subStat[subFull];
    return Math.max(-0.4, Math.min(1.4, 0.5 + (r - st.m) / (2.2 * st.sd)));
  }
  function sev(coreFull, subFull) {
    var r = raw(coreFull, subFull); if (r === null) r = fillMean[coreFull];
    return Math.max(0, Math.min(1, (r - 0.6) / 0.4));
  }
  function lerpCol(t) {
    var a = [0.18, 0.63, 0.26], b = [0.82, 0.60, 0.13], c = [0.97, 0.32, 0.29];
    var lo = t < 0.5 ? a : b, hi = t < 0.5 ? b : c, u = t < 0.5 ? t * 2 : (t - 0.5) * 2;
    return [lo[0] + (hi[0] - lo[0]) * u, lo[1] + (hi[1] - lo[1]) * u, lo[2] + (hi[2] - lo[2]) * u];
  }
  function field(vals, accessor) {
    return function (x, z) {
      var n = 0, d = 0;
      for (var i = 0; i < SUBS.length; i++) {
        var s = SUBS[i], w = 1 / ((x - s.x) * (x - s.x) + (z - s.z) * (z - s.z) + 1.2);
        n += vals[i] * w; d += w;
      }
      return n / d;
    };
  }

  // ---- renderer ----
  var W = wrap.clientWidth || 1000, H = canvas.clientHeight || 420, renderer;
  try { renderer = new THREE.WebGLRenderer({ canvas: canvas, antialias: true }); }
  catch (e) { return fail('3D view requires WebGL.'); }
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setSize(W, H, false);
  var scene = new THREE.Scene();
  var camera = new THREE.PerspectiveCamera(46, W / H, 0.1, 500);
  var HOME = new THREE.Vector3(0, 4.2, 13.5); camera.position.copy(HOME);
  var controls = new THREE.OrbitControls(camera, canvas);
  controls.enableDamping = true; controls.dampingFactor = 0.08;
  controls.minDistance = 6; controls.maxDistance = 30;
  controls.autoRotate = true; controls.autoRotateSpeed = 0.9;
  controls.target.set(0, 0, 0); controls.update();
  var group = new THREE.Group(); scene.add(group);
  var guideGrp = new THREE.Group(); scene.add(guideGrp);
  var meshes = [];

  function build() {
    meshes.forEach(function (m) { group.remove(m); }); meshes.length = 0;
    CORES.forEach(function (c, li) {
      var hv = SUBS.map(function (s) { return height(c.full, s.full); });
      var cv = SUBS.map(function (s) { return sev(c.full, s.full); });
      var fh = field(hv), fc = field(cv);
      var g = new THREE.PlaneGeometry(12, 8, 40, 26); g.rotateX(-Math.PI / 2);
      var p = g.attributes.position, cols = [];
      for (var i = 0; i < p.count; i++) {
        var x = p.getX(i), z = p.getZ(i);
        p.setY(i, fh(x, z) * AMP);
        var rgb = lerpCol(Math.max(0, Math.min(1, fc(x, z))));
        cols.push(rgb[0], rgb[1], rgb[2]);
      }
      g.setAttribute('color', new THREE.Float32BufferAttribute(cols, 3));
      var mesh = new THREE.Mesh(g, new THREE.MeshBasicMaterial(
        { wireframe: true, vertexColors: true, transparent: true, opacity: 0.95 }));
      mesh.position.y = (1.5 - li) * GAP; mesh.visible = c._off !== true;
      group.add(mesh); meshes.push(mesh);
    });
  }
  function buildGuides() {
    while (guideGrp.children.length) guideGrp.remove(guideGrp.children[0]);
    if (!document.getElementById('freq-guides').checked) return;
    var yTop = 1.5 * GAP + AMP * 1.4, yBot = (1.5 - (CORES.length - 1)) * GAP - 0.4;
    SUBS.forEach(function (s) {
      var geo = new THREE.BufferGeometry().setFromPoints(
        [new THREE.Vector3(s.x, yBot, s.z), new THREE.Vector3(s.x, yTop, s.z)]);
      guideGrp.add(new THREE.Line(geo, new THREE.LineBasicMaterial(
        { color: 0x2b3442, transparent: true, opacity: 0.55 })));
    });
  }

  // ---- layer overlay ----
  var layersEl = document.getElementById('freq-layers');
  var allbtn = document.getElementById('freq-showall');
  CORES.forEach(function (c, i) {
    var row = document.createElement('div'); row.className = 'lrow'; row.setAttribute('data-i', i);
    var hex = ['#2ea043', '#4f9bd9', '#d29922', '#f85149'][i % 4];
    row.innerHTML = '<span class="dot" style="background:' + hex + '"></span>'
      + '<span class="ln">' + c.name + '</span>'
      + '<span class="solo" data-solo>solo</span><span class="eye">●</span>';
    row.addEventListener('click', function (e) {
      if (e.target.hasAttribute('data-solo')) { solo(i); return; }
      c._off = c._off !== true ? true : false; refresh();
    });
    layersEl.insertBefore(row, allbtn);
  });
  function solo(i) { CORES.forEach(function (c, j) { c._off = (j !== i); }); refresh(); }
  allbtn.addEventListener('click', function () { CORES.forEach(function (c) { c._off = false; }); refresh(); });
  function refresh() {
    CORES.forEach(function (c, i) {
      meshes[i].visible = c._off !== true;
      var row = layersEl.querySelector('.lrow[data-i="' + i + '"]');
      if (row) row.classList.toggle('off', c._off === true);
    });
  }

  build(); buildGuides(); refresh();
  (function loop() { controls.update(); renderer.render(scene, camera); requestAnimationFrame(loop); })();
  window.addEventListener('resize', function () {
    W = wrap.clientWidth || 1000; if (W <= 0) return;
    renderer.setSize(W, H, false); camera.aspect = W / H; camera.updateProjectionMatrix();
  });

  function $(id) { return document.getElementById(id); }
  $('freq-mode').addEventListener('change', function (e) { MODE = e.target.value; build(); buildGuides(); refresh(); });
  $('freq-amp').addEventListener('input', function (e) { AMP = +e.target.value; build(); buildGuides(); refresh(); });
  $('freq-gap').addEventListener('input', function (e) { GAP = +e.target.value; build(); buildGuides(); refresh(); });
  $('freq-guides').addEventListener('change', buildGuides);
  $('freq-spin').addEventListener('change', function (e) { controls.autoRotate = e.target.checked; });
  $('freq-reset').addEventListener('click', function () {
    controls.autoRotate = $('freq-spin').checked; camera.position.copy(HOME);
    controls.target.set(0, 0, 0); controls.update();
  });
  $('freq-top').addEventListener('click', function () {
    controls.autoRotate = false; $('freq-spin').checked = false;
    camera.position.set(0, 20, 0.01); controls.target.set(0, 0, 0); controls.update();
  });
})();
```

- [ ] **Step 2: Commit (mounting happens in Task 8)**

```bash
git add mai-data/static/js/frequency3d.js
git commit -m "feat: playable 3D drift component (orbit/zoom/pan, modes, toggle/solo)"
```

### Task 8: The `freq3d.html` partial + mount on dashboard & drift page

**Files:**
- Create: `mai-data/layouts/partials/freq3d.html`
- Modify: `mai-data/layouts/index.html` (swap the placeholder), `mai-data/layouts/sync/list.html`

- [ ] **Step 1: Create the partial**

```html
<div id="freq" class="freq-hero">
  <div class="hh"><div><h3>Cross-core drift</h3>
    <span class="sub">each layer = a core · ridges rise where it diverges most per subsystem</span></div>
    <a href="/sync/">Full drift page →</a></div>
  <canvas id="freq-c"></canvas>
  <div id="freq-layers" class="freq-layers"><div class="lt">Cores — click to toggle</div>
    <button class="allbtn" id="freq-showall">Show all</button></div>
  <div id="freq-err" class="freq-err"></div>
  <div class="freq-ctrls">
    <label>Height
      <select id="freq-mode">
        <option value="contrast" selected>Contrast</option>
        <option value="relative">Relative</option>
        <option value="absolute">Absolute</option>
      </select></label>
    <label>Peak <input id="freq-amp" type="range" min="0.6" max="3.0" step="0.1" value="1.8"></label>
    <label>Spacing <input id="freq-gap" type="range" min="0.6" max="2.8" step="0.1" value="1.4"></label>
    <label><input id="freq-guides" type="checkbox" checked> guides</label>
    <label><input id="freq-spin" type="checkbox" checked> spin</label>
    <button class="fbtn" id="freq-reset">Reset</button>
    <button class="fbtn" id="freq-top">Top-down</button>
  </div>
  <div class="freq-legend">low <span class="hm-grad"></span> high divergence · drag rotate · scroll zoom · right-drag pan</div>
</div>
<script>window.MAI_FREQ = {{ with .Site.Data.frequency }}{{ . | jsonify | safeJS }}{{ else }}null{{ end }};</script>
<script src="/js/vendor/three.min.js"></script>
<script src="/js/vendor/OrbitControls.js"></script>
<script src="/js/frequency3d.js"></script>
```

- [ ] **Step 2: Swap the dashboard placeholder**

In `mai-data/layouts/index.html`, replace the entire `{{/* HERO PLACEHOLDER … */}}` panel block (the comment plus the `<div class="panel">…</div>` that renders the `hm` table) with:

```html
  {{ partial "freq3d.html" . }}
```

- [ ] **Step 3: Replace `sync/list.html` to use the partial**

```html
{{ define "main" }}
  <h1>Cross-core drift</h1>
  <p class="muted">Each layer is a core; the surface rises where that core diverges most per subsystem.</p>
  {{ partial "freq3d.html" . }}
  <h2 style="margin-top:22px">Per-pair drift</h2>
  <ul class="core-list">
    {{ range .Pages }}<li><a href="{{ .RelPermalink }}">{{ .Title }}</a></li>{{ end }}
  </ul>
{{ end }}
```

- [ ] **Step 4: Verify build wires the object + scripts**

Run:
```bash
cd mai-data && hugo --quiet --destination /tmp/h8 \
  && grep -q 'window.MAI_FREQ = {"cores"' /tmp/h8/index.html \
  && grep -q '/js/vendor/OrbitControls.js' /tmp/h8/index.html \
  && grep -q 'freq-mode' /tmp/h8/sync/index.html && echo OK
```
Expected: `OK` (object literal, not a quoted string; scripts present on both pages). Requires a populated `data/frequency.json`.

- [ ] **Step 5: Manual check**

Run `cd mai-data && hugo server` and open `/`. Confirm: surfaces centered & ridged, color green→red, drag/scroll/right-drag work, toggle/solo work, **Top-down then Reset** works (no stuck view), mode switch changes shape. Stop the server.

- [ ] **Step 6: Commit**

```bash
git add mai-data/layouts/partials/freq3d.html mai-data/layouts/index.html mai-data/layouts/sync/list.html
git commit -m "feat: mount playable 3D drift hero on dashboard and drift page"
```

---

## Phase 3 — Porting board

### Task 9: `build_pushes` — recent merged PRs per core

**Files:**
- Modify: `src/mai/publish/dataviz.py` (add `build_pushes`)
- Test: `tests/test_dataviz.py`

- [ ] **Step 1: Write the failing test**

```python
async def test_build_pushes_groups_merged_prs_by_core(session):
    from mai.publish.dataviz import build_pushes
    await ingest_event(session, IntakeEvent(
        "gh_pr", "mangosthree/server#142", "corpse loot drop fix", "three",
        status="merged", repo_full_name="mangosthree/server",
        raw_payload={"merged_at": "2026-06-10T00:00:00Z",
                     "html_url": "https://github.com/mangosthree/server/pull/142", "number": 142}))
    await ingest_event(session, IntakeEvent(
        "gh_pr", "mangosthree/server#9", "open spell pr", "three",
        status="open", repo_full_name="mangosthree/server",
        raw_payload={"html_url": "x", "number": 9}))
    await session.commit()
    p = await build_pushes(session, limit=8)
    three = next(c for c in p["cores"] if c["core"] == "three")
    prs = three["pushes"]
    assert [x["pr"] for x in prs] == [142]           # only merged
    assert prs[0]["area"] == "Loot"                  # "corpse"/"loot"/"drop" -> Loot
    assert prs[0]["url"].endswith("/pull/142")
    assert prs[0]["repo"] == "mangosthree/server"
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_dataviz.py::test_build_pushes_groups_merged_prs_by_core -v`
Expected: FAIL (`cannot import name 'build_pushes'`).

- [ ] **Step 3: Implement**

Add to `src/mai/publish/dataviz.py` (uses existing imports `select`, `Report`, `area_of`; add `ReportSourceMap, SourceRecord` to the models import):

```python
async def _latest_payload(session: AsyncSession, source_type: str, source_id: str) -> dict:
    rec = await session.scalar(
        select(SourceRecord)
        .where(SourceRecord.source_type == source_type, SourceRecord.source_id == source_id)
        .order_by(SourceRecord.version.desc()).limit(1))
    return rec.payload if rec else {}


async def build_pushes(session: AsyncSession, limit: int = 8) -> dict:
    """Recent merged PRs grouped by core, for the porting board's 'what landed' columns."""
    rows = await session.scalars(
        select(Report).where(Report.canonical_key.like("gh_pr:%"),
                             Report.status == "merged"))
    by_core: dict[str, list] = {}
    for r in rows:
        source_id = r.canonical_key[len("gh_pr:"):]
        payload = await _latest_payload(session, "gh_pr", source_id)
        merged_at = payload.get("merged_at") or ""
        repo = source_id.split("#")[0]
        by_core.setdefault(r.core, []).append({
            "title": r.title,
            "area": area_of(r.title, None, payload),
            "pr": payload.get("number"),
            "url": payload.get("html_url", ""),
            "repo": repo,
            "merged_at": merged_at,
        })
    cores = []
    for core, pushes in sorted(by_core.items()):
        pushes.sort(key=lambda p: p["merged_at"], reverse=True)
        repo = pushes[0]["repo"] if pushes else ""
        cores.append({"core": core, "repo": repo, "pushes": pushes[:limit]})
    return {"cores": cores}
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_dataviz.py::test_build_pushes_groups_merged_prs_by_core -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mai/publish/dataviz.py tests/test_dataviz.py
git commit -m "feat: build_pushes — recent merged PRs grouped by core"
```

### Task 10: Write `pushes.json` in `write_dataviz`

**Files:**
- Modify: `src/mai/publish/dataviz.py` (`write_dataviz`)
- Test: `tests/test_dataviz.py`

- [ ] **Step 1: Update the file-count test**

Change `test_write_dataviz_writes_three_files` to expect four files:

```python
async def test_write_dataviz_writes_four_files(session, tmp_path):
    from mai.publish.dataviz import write_dataviz
    await DriftRepository(session).upsert(
        "mangoszero/server", "mangostwo/server", "src/game/Object",
        {"shared": 10, "diverged": 5, "identical": 5, "only_a": 0, "only_b": 0})
    await session.commit()
    await write_dataviz(session, str(tmp_path))
    for name in ("drift.json", "dashboard.json", "frequency.json", "pushes.json"):
        assert (tmp_path / "data" / name).exists()
```

(Delete the old three-file test.)

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_dataviz.py::test_write_dataviz_writes_four_files -v`
Expected: FAIL (`pushes.json` missing).

- [ ] **Step 3: Implement**

In `write_dataviz`, add after the `frequency.json` write:

```python
    (data / "pushes.json").write_text(
        json.dumps(await build_pushes(session), indent=2), encoding="utf-8")
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_dataviz.py -v`
Expected: PASS (all dataviz tests).

- [ ] **Step 5: Commit**

```bash
git add src/mai/publish/dataviz.py tests/test_dataviz.py
git commit -m "feat: write pushes.json in publish dataviz"
```

### Task 11: `board.js` — drag/drop + localStorage

**Files:**
- Create: `mai-data/static/js/board.js`

- [ ] **Step 1: Create the file**

```javascript
(function () {
  var P = window.MAI_PUSHES;
  var board = document.getElementById('board');
  if (!board || !P || !P.cores) return;
  var AREA = { Movement: '#0969da', Spell: '#8250df', Combat: '#cf222e', Quest: '#1a7f37',
    Loot: '#9a6700', Item: '#bc4c00', Creature: '#bf3989', Character: '#6639ba',
    World: '#0c7489', Database: '#57606a', Tools: '#424a53', Network: '#4f46c4', Other: '#59636e' };
  function pill(a) { var c = AREA[a] || '#59636e';
    return '<span class="bpill" style="background:' + c + '1f;color:' + c + '">' + a + '</span>'; }
  var CORES = ['zero', 'one', 'two', 'three'];
  var byCore = {}; P.cores.forEach(function (c) { byCore[c.core] = c.pushes || []; });

  // fill per-core columns
  CORES.forEach(function (core) {
    var col = board.querySelector('.col[data-core="' + core + '"]'); if (!col) return;
    var drop = col.querySelector('.cards'); var list = byCore[core] || [];
    col.querySelector('.ct').textContent = list.length + ' pushes';
    if (!list.length) { drop.innerHTML = '<div class="empty-state">no recent merges</div>'; return; }
    list.forEach(function (it) {
      var d = document.createElement('div'); d.className = 'bcard'; d.draggable = true;
      d.setAttribute('data-pr', it.pr); d.setAttribute('data-core', core);
      d.setAttribute('data-title', it.title); d.setAttribute('data-area', it.area);
      d.innerHTML = '<div class="ct">' + it.title + '</div><div class="cm">' + pill(it.area)
        + '<span class="bpr">' + core + ' · PR #' + it.pr + '</span></div>';
      d.addEventListener('dragstart', function () { dragging = d; d.classList.add('dragging'); });
      d.addEventListener('dragend', function () { dragging = null; d.classList.remove('dragging'); });
      drop.appendChild(d);
    });
  });

  // TODO lane
  var todo = document.getElementById('todo'), todoct = document.getElementById('todoct');
  var dragging = null;
  todo.addEventListener('dragover', function (e) { e.preventDefault(); todo.classList.add('over'); });
  todo.addEventListener('dragleave', function () { todo.classList.remove('over'); });
  todo.addEventListener('drop', function (e) {
    e.preventDefault(); todo.classList.remove('over');
    if (!dragging) return;
    add({ core: dragging.getAttribute('data-core'), title: dragging.getAttribute('data-title'),
      area: dragging.getAttribute('data-area'), pr: dragging.getAttribute('data-pr') });
  });
  function load() { try { return JSON.parse(localStorage.getItem('mai.porting') || '[]'); } catch (e) { return []; } }
  function save() { try { localStorage.setItem('mai.porting', JSON.stringify(items)); } catch (e) {} }
  var items = load();
  function targetOpts(from) {
    return CORES.filter(function (c) { return c !== from; })
      .map(function (c) { return '<option>' + c + '</option>'; }).join('');
  }
  function render() {
    if (!items.length) { todo.innerHTML = '<div class="empty-state">Drag fixes here to build a porting checklist ⤵</div>'; }
    else {
      todo.innerHTML = items.map(function (it) {
        return '<div class="bcard tcard' + (it.done ? ' done' : '') + '">'
          + '<span class="x" title="remove">×</span>'
          + '<div class="ct">' + it.title + '</div>'
          + '<div class="cm">' + pill(it.area) + '<span class="bpr">from ' + it.core + ' · PR #' + it.pr + '</span></div>'
          + '<div class="src"><span class="arrow">→ port to</span>'
          + '<select class="target">' + targetOpts(it.core) + '</select>'
          + '<label class="donebox"><input type="checkbox"' + (it.done ? ' checked' : '') + '> done</label></div></div>';
      }).join('');
    }
    todoct.textContent = items.length;
    Array.prototype.forEach.call(todo.querySelectorAll('.x'), function (x, i) {
      x.onclick = function () { items.splice(i, 1); save(); render(); };
    });
    Array.prototype.forEach.call(todo.querySelectorAll('.target'), function (s, i) {
      if (items[i].target) s.value = items[i].target;
      s.onchange = function () { items[i].target = s.value; save(); };
    });
    Array.prototype.forEach.call(todo.querySelectorAll('.donebox input'), function (cb, i) {
      cb.onchange = function () { items[i].done = cb.checked; save(); render(); };
    });
  }
  function add(it) {
    if (items.some(function (x) { return x.pr === it.pr && x.core === it.core; })) return;
    it.target = CORES.filter(function (c) { return c !== it.core; })[0];
    it.done = false; items.push(it); save(); render();
  }
  render();
})();
```

- [ ] **Step 2: Commit (mounting in Task 12)**

```bash
git add mai-data/static/js/board.js
git commit -m "feat: porting board drag/drop + localStorage component"
```

### Task 12: Mount the porting board on the dashboard

**Files:**
- Modify: `mai-data/layouts/index.html`

- [ ] **Step 1: Insert the board between the 3D hero and the `.two` secondary row**

After `{{ partial "freq3d.html" . }}` and before `<div class="two">`, add:

```html
  {{ with .Site.Data.pushes }}
  <div class="board-head">
    <h2>Porting board — what landed where</h2>
    <span class="hint">Drag a fix into <b>Porting TODO</b> to track what needs carrying core→core. Saved in your browser.</span>
  </div>
  <div class="board" id="board">
    <div class="col" data-core="zero"><div class="colh"><span class="cname">Zero</span><span class="ct"></span></div><div class="cards"></div></div>
    <div class="col" data-core="one"><div class="colh"><span class="cname">One</span><span class="ct"></span></div><div class="cards"></div></div>
    <div class="col" data-core="two"><div class="colh"><span class="cname">Two</span><span class="ct"></span></div><div class="cards"></div></div>
    <div class="col" data-core="three"><div class="colh"><span class="cname">Three</span><span class="ct"></span></div><div class="cards"></div></div>
    <div class="col todo"><div class="colh"><span class="cname">⤷ Porting TODO</span><span class="ct" id="todoct">0</span></div><div class="cards" id="todo"></div></div>
  </div>
  <div class="board-foot">Cards = recently merged fixes per core. The TODO list is personal and persists locally.</div>
  <script>window.MAI_PUSHES = {{ . | jsonify | safeJS }};</script>
  <script src="/js/board.js"></script>
  {{ end }}
```

- [ ] **Step 2: Verify build + wiring**

Run:
```bash
cd mai-data && hugo --quiet --destination /tmp/h12 \
  && grep -q 'id="board"' /tmp/h12/index.html \
  && grep -q 'window.MAI_PUSHES = {"cores"' /tmp/h12/index.html \
  && grep -q '/js/board.js' /tmp/h12/index.html && echo OK
```
Expected: `OK`. Requires a populated `data/pushes.json` (run `python -m mai.cli publish` against the db first).

- [ ] **Step 3: Manual check**

`cd mai-data && hugo server`, open `/`: drag a card into Porting TODO, set a target, tick "done", reload → it persists. Remove with ×. Stop server.

- [ ] **Step 4: Commit**

```bash
git add mai-data/layouts/index.html
git commit -m "feat: mount porting board on dashboard"
```

---

## Phase 4 — Polish & verify

### Task 13: Full suite + populated end-to-end smoke

**Files:** none (verification only)

- [ ] **Step 1: Run the whole Python test suite**

Run: `python -m pytest -q`
Expected: all pass (≈ existing count + the new dataviz tests). If any fail, fix before continuing.

- [ ] **Step 2: Regenerate data and build the real site**

Run:
```bash
python -m mai.cli publish
cd mai-data && hugo --quiet --destination public && echo "pages built:" && find public -name "*.html" | wc -l
```
Expected: build succeeds; `data/{dashboard,frequency,pushes,drift}.json` all present; HTML page count ≥ prior (82).

- [ ] **Step 3: Confirm degradation paths**

Temporarily rename `mai-data/data/frequency.json`, rebuild, confirm the 3D panel shows the fallback text (not a blank/broken page); restore the file. Confirm a core column with no merges shows "no recent merges".

- [ ] **Step 4: Commit any fixes**

```bash
git add -A
git commit -m "test: full suite + populated build verification for web v2"
```

### Task 14: Mark the spec Built

**Files:**
- Modify: `docs/specs/dashboard-workspace-redesign.md`

- [ ] **Step 1: Flip status**

Change front-matter `status: Draft` → `status: Built` and `version: 0.1` → `version: 1.0`.

- [ ] **Step 2: Commit**

```bash
git add docs/specs/dashboard-workspace-redesign.md
git commit -m "docs: mark dashboard workspace redesign spec Built (v1.0)"
```

---

## Self-Review

**Spec coverage:** dashboard restructure (Tasks 2–4, 8, 12) · playable 3D with modes/toggles/orbit (Tasks 5–8) · contrast-stretch + absolute color, client-side (Task 7) · porting board + drag-to-TODO + localStorage (Tasks 9–12) · pushes.json via existing harvest data (Tasks 9–10) · coverage strip (Tasks 1, 4) · vendored libs (Task 6) · graceful degradation (Task 13). All spec sections map to a task.

**Type/name consistency:** `build_pushes(session, limit=8)` returns `{"cores":[{core, repo, pushes:[{title, area, pr, url, repo, merged_at}]}]}`; `board.js` reads `window.MAI_PUSHES.cores[].pushes[].{title,area,pr}`. The 3D component reads `window.MAI_FREQ.{cores[].{name,full,y}, subsystems[].{name,full,x,z}, intensity[full][full]}` (raw ratio or null) — matches `build_frequency`'s output. Element ids (`freq-c`, `freq-layers`, `freq-mode`, `freq-amp`, `freq-gap`, `freq-guides`, `freq-spin`, `freq-reset`, `freq-top`, `freq-showall`, `board`, `todo`, `todoct`) are consistent between `freq3d.html`/`index.html` and the JS. `safeJS` is used for every embedded JSON object.

**Placeholder scan:** no TBD/TODO; every code step shows full content; JS files are complete (no test harness exists for browser JS, so those tasks use build-grep + manual checks, which is called out explicitly).
