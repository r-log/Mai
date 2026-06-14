# Web Redesign Phase B — Dashboard + Drift Heatmap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the home dashboard — stat cards, a color-coded cross-core drift heatmap, a top-areas breakdown, and a recently-confirmed-fixed strip — driven by JSON data Mai generates from the DB at publish time.

**Architecture:** A new `publish/dataviz.py` aggregates the DB into two JSON files Hugo auto-loads as `.Site.Data` (`drift.json` = the heatmap matrix with **Python-computed cell colors**; `dashboard.json` = stats + top areas + recent fixes). `publish_site` writes them alongside the content. The dashboard `index.html` renders everything **server-side** (no JS) — the heatmap is a colored HTML table. CSS extends `mai.css`.

**Tech Stack:** Python 3.12 · SQLAlchemy 2.0 async · pytest + pytest-asyncio · Hugo (`.Site.Data` + templates) · stdlib `json`.

---

## Builds on Plans 01–09

Reuse as-is (do NOT redefine): `mai.publish.views` (`counts`, `iter_bug_reports`, `report_bundle`), `mai.publish.areas.AREAS`, `mai.repository.{drift.DriftRepository, correlation.VerificationRepository, reports.ReportRepository}`, `mai.publish.site.publish_site` (extended here), `tests/conftest.py`.

**Design principles (spec §4):** static & offline (heatmap is server-rendered HTML, no JS); data is generated, not hardcoded; one CSS token set; graceful when data is missing.

## File Structure

```
src/mai/publish/
  dataviz.py            # NEW: heat_hex, build_drift_matrix, build_dashboard, write_dataviz
  site.py               # MODIFY: publish_site also writes the dataviz JSON
mai-data/
  layouts/index.html    # MODIFY: dashboard (stat cards + heatmap + top areas + recent fixes)
  static/css/mai.css    # MODIFY (append): dashboard component styles
.gitignore              # MODIFY: ignore mai-data/data/ (generated)
tests/
  test_dataviz.py
  (test_publish_site.py — add a data-files assertion)
```

---

### Task 1: dataviz aggregation (Python)

**Files:**
- Create: `mai/src/mai/publish/dataviz.py`
- Create: `mai/tests/test_dataviz.py`

- [ ] **Step 1: Write the failing test**

`mai/tests/test_dataviz.py`:

```python
from mai.contracts import IntakeEvent
from mai.ingest import ingest_event
from mai.publish.dataviz import build_dashboard, build_drift_matrix, heat_hex
from mai.repository.correlation import VerificationRepository
from mai.repository.drift import DriftRepository
from mai.repository.reports import ReportRepository


def test_heat_hex_is_hex_and_redder_when_higher():
    assert heat_hex(70).startswith("#") and len(heat_hex(70)) == 7
    r_lo, r_hi = int(heat_hex(58)[1:3], 16), int(heat_hex(88)[1:3], 16)
    g_lo, g_hi = int(heat_hex(58)[3:5], 16), int(heat_hex(88)[3:5], 16)
    assert r_hi >= r_lo and g_hi <= g_lo   # higher % -> more red, less green


async def test_build_drift_matrix_aggregates_and_colors(session):
    d = DriftRepository(session)
    await d.upsert("mangoszero/server", "mangostwo/server", "src/game/Object",
                   {"shared": 80, "diverged": 60, "identical": 20, "only_a": 0, "only_b": 0})
    await d.upsert("mangoszero/server", "mangostwo/server", "src/shared",
                   {"shared": 20, "diverged": 4, "identical": 16, "only_a": 0, "only_b": 0})
    await session.commit()
    m = await build_drift_matrix(session)
    assert set(m["cores"]) == {"Zero", "Two"}
    cells = [c for row in m["rows"] for c in row["cells"] if not c.get("self")]
    vals = [c["value"] for c in cells if c.get("value") is not None]
    assert 64 in vals   # (60+4)/(80+20) = 64 %
    assert all(c["color"].startswith("#") for c in cells if c.get("value") is not None)
    # diagonal is a self cell
    assert any(c.get("self") for row in m["rows"] for c in row["cells"])


async def test_build_dashboard_summarizes(session):
    await ingest_event(session, IntakeEvent("ips", "r1", "Pet bug", "three",
                                            raw_payload={"markdown": "x"}))
    await ingest_event(session, IntakeEvent("gh_pr", "mangosthree/server#7", "Fix", "three",
                                            status="merged", raw_payload={"body": "y"}))
    await session.commit()
    bug = await ReportRepository(session).get_report("ips:r1")
    await VerificationRepository(session).upsert(
        bug.id, "fixed_confirmed", 0.95, [{"related": "gh_pr:mangosthree/server#7"}])
    await session.commit()
    dash = await build_dashboard(session)
    assert dash["stats"]["reports"] == 2
    assert dash["stats"]["fixed_confirmed"] == 1
    assert any(a["name"] == "Creature" for a in dash["top_areas"])   # "Pet bug" -> Creature
    rf = dash["recently_fixed"][0]
    assert rf["id"] == "ips:r1"
    assert rf["related"] == "gh_pr:mangosthree/server#7"
    assert rf["url"] == "/three/bugs/ips-r1/"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mai && pytest tests/test_dataviz.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mai.publish.dataviz'`

- [ ] **Step 3: Write `publish/dataviz.py`**

```python
import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mai.db.models import DriftObservation, Verification
from mai.publish.areas import AREAS
from mai.publish.views import counts, iter_bug_reports, report_bundle
from mai.repository.reports import ReportRepository

_AREA_COLOR = {a["name"]: a["color"] for a in AREAS}
_STOPS = [(0x2e, 0xa0, 0x43), (0xd2, 0x99, 0x22), (0xf8, 0x51, 0x49)]  # green, amber, red


def _safe(key: str) -> str:
    return key.replace(":", "-").replace("/", "-").replace("#", "-")


def _short_core(full_name: str) -> str:
    org = full_name.split("/")[0]
    return (org[len("mangos"):] if org.startswith("mangos") else org).title() or full_name


def heat_hex(pct: float) -> str:
    """Map a divergence percentage (~55..90) to a green->amber->red hex color."""
    t = max(0.0, min(1.0, (pct - 55) / 35.0))
    lo, hi, u = (_STOPS[0], _STOPS[1], t * 2) if t < 0.5 else (_STOPS[1], _STOPS[2], (t - 0.5) * 2)
    rgb = tuple(round(lo[i] + (hi[i] - lo[i]) * u) for i in range(3))
    return "#%02x%02x%02x" % rgb


async def build_drift_matrix(session: AsyncSession) -> dict:
    agg: dict[tuple[str, str], dict] = {}
    for o in await session.scalars(select(DriftObservation)):
        key = tuple(sorted((o.fork_a, o.fork_b)))
        bucket = agg.setdefault(key, {"shared": 0, "diverged": 0})
        bucket["shared"] += o.shared
        bucket["diverged"] += o.diverged
    cores = sorted({c for key in agg for c in key})
    rows = []
    for a in cores:
        cells = []
        for b in cores:
            if a == b:
                cells.append({"self": True})
                continue
            bucket = agg.get(tuple(sorted((a, b))))
            if bucket and bucket["shared"]:
                pct = round(100 * bucket["diverged"] / bucket["shared"])
                cells.append({"value": pct, "color": heat_hex(pct)})
            else:
                cells.append({"value": None})
        rows.append({"core": _short_core(a), "full": a, "cells": cells})
    return {"cores": [_short_core(c) for c in cores], "rows": rows}


async def build_dashboard(session: AsyncSession) -> dict:
    stats = await counts(session)
    area_counts: dict[str, int] = {}
    for report in await iter_bug_reports(session):
        bundle = await report_bundle(session, report)
        area_counts[bundle.area] = area_counts.get(bundle.area, 0) + 1
    top_areas = [{"name": name, "count": n, "color": _AREA_COLOR.get(name, "#59636e")}
                 for name, n in sorted(area_counts.items(), key=lambda kv: kv[1], reverse=True)]
    fixed = []
    rr = ReportRepository(session)
    vs = await session.scalars(
        select(Verification).where(Verification.verdict == "fixed_confirmed").limit(10))
    for v in vs:
        rep = await rr.get_by_id(v.report_id)
        if rep is None:
            continue
        related = v.evidence[0]["related"] if v.evidence else ""
        fixed.append({"id": rep.canonical_key, "title": rep.title, "core": rep.core,
                      "related": related, "url": f"/{rep.core}/bugs/{_safe(rep.canonical_key)}/"})
    return {"stats": stats, "top_areas": top_areas, "recently_fixed": fixed}


async def write_dataviz(session: AsyncSession, out_dir: str) -> None:
    data = Path(out_dir) / "data"
    data.mkdir(parents=True, exist_ok=True)
    (data / "drift.json").write_text(
        json.dumps(await build_drift_matrix(session), indent=2), encoding="utf-8")
    (data / "dashboard.json").write_text(
        json.dumps(await build_dashboard(session), indent=2), encoding="utf-8")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd mai && pytest tests/test_dataviz.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add mai/src/mai/publish/dataviz.py mai/tests/test_dataviz.py
git commit -m "feat: dataviz aggregation (drift matrix + dashboard json, server-colored cells)"
```

---

### Task 2: Wire dataviz into publish + ignore generated data

**Files:**
- Modify: `mai/src/mai/publish/site.py` (`publish_site` calls `write_dataviz`)
- Modify: `mai/.gitignore` (ignore `mai-data/data/`)
- Modify: `mai/tests/test_publish_site.py` (assert data files)

- [ ] **Step 1: Add the failing assertion**

In `mai/tests/test_publish_site.py`, add this test:

```python
async def test_publish_site_writes_dataviz_json(session, tmp_path):
    from mai.repository.drift import DriftRepository
    await ingest_event(session, IntakeEvent("ips", "r1", "Bug", "three",
                                            raw_payload={"markdown": "x"}))
    await DriftRepository(session).upsert("mangoszero/server", "mangostwo/server",
                                          "src/game/Object", STATS)
    await session.commit()
    await publish_site(session, str(tmp_path))
    assert (tmp_path / "data" / "drift.json").exists()
    assert (tmp_path / "data" / "dashboard.json").exists()
```

(`STATS` and the other imports already exist at the top of that test file from Plan 08.)

- [ ] **Step 2: Run it to verify it fails**

Run: `cd mai && pytest tests/test_publish_site.py::test_publish_site_writes_dataviz_json -v`
Expected: FAIL (data files not written yet).

- [ ] **Step 3: Call `write_dataviz` from `publish_site`**

In `mai/src/mai/publish/site.py`: add the import and the call at the end of `publish_site` (before `return written`):

```python
from mai.publish.dataviz import write_dataviz
```
and, just before `return written`:
```python
    await write_dataviz(session, out_dir)
```

- [ ] **Step 4: Ignore the generated data dir**

In `mai/.gitignore`, add `mai-data/data/` next to the other `mai-data/` ignores:

```
mai-data/data/
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd mai && pytest tests/test_publish_site.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add mai/src/mai/publish/site.py mai/.gitignore mai/tests/test_publish_site.py
git commit -m "feat: publish writes dataviz json; ignore generated mai-data/data"
```

---

### Task 3: Dashboard template (`index.html`)

**Files:**
- Modify: `mai/mai-data/layouts/index.html`

- [ ] **Step 1: Replace `index.html`**

```html
{{ define "main" }}
  {{ $d := .Site.Data.dashboard }}
  {{ $drift := .Site.Data.drift }}
  <h1>getMaNGOS Bug &amp; Drift Observatory</h1>

  {{ with $d }}
  <div class="stats">
    <div class="stat"><div class="n">{{ .stats.reports }}</div><div class="l">Reports</div></div>
    <div class="stat g"><div class="n">{{ .stats.fixed_confirmed }}</div><div class="l">Fixed</div></div>
    <div class="stat y"><div class="n">{{ .stats.likely_fixed }}</div><div class="l">Likely fixed</div></div>
    <div class="stat"><div class="n">{{ .stats.open }}</div><div class="l">Open</div></div>
    <div class="stat b"><div class="n">{{ .stats.drift_pairs }}</div><div class="l">Drift pairs</div></div>
  </div>
  {{ end }}

  <div class="dash-grid">
    <div class="panel">
      <div class="panel-h">Cross-core drift — % of shared files diverged</div>
      <div class="panel-b">
        {{ with $drift }}
        <table class="hm">
          <tr><th></th>{{ range .cores }}<th>{{ . }}</th>{{ end }}</tr>
          {{ range .rows }}
            <tr><td class="hm-l">{{ .core }}</td>
              {{ range .cells }}
                {{ if .self }}<td class="hm-c na">—</td>
                {{ else if .value }}<td class="hm-c" style="background:{{ .color }}">{{ .value }}</td>
                {{ else }}<td class="hm-c na"></td>{{ end }}
              {{ end }}
            </tr>
          {{ end }}
        </table>
        <div class="hm-legend">low <span class="hm-grad"></span> high divergence</div>
        {{ else }}<p class="muted">No drift data yet — run <code>mai drift</code>.</p>{{ end }}
      </div>
    </div>

    <div class="panel">
      <div class="panel-h">Top areas</div>
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
  </div>

  <div class="panel" style="margin-top:16px">
    <div class="panel-h">Recently confirmed fixed</div>
    <div class="panel-b">
      {{ range $d.recently_fixed }}
        <div class="rfix">&#10004; <a href="{{ .url }}">{{ .title }}</a>
          <span class="muted">— {{ .id }} → {{ .related }}</span></div>
      {{ else }}
        <p class="muted">No confirmed fixes yet.</p>
      {{ end }}
    </div>
  </div>

  <h2 style="margin-top:22px">Cores</h2>
  <ul class="core-list">
    {{ range .Site.Sections }}{{ if ne .Section "sync" }}
      <li><a href="{{ .RelPermalink }}">{{ .Title }}</a> <span class="muted">({{ len .Pages }})</span></li>
    {{ end }}{{ end }}
  </ul>
{{ end }}
```

- [ ] **Step 2: Commit**

```bash
git add mai/mai-data/layouts/index.html
git commit -m "feat: dashboard (stat cards + drift heatmap + top areas + recent fixes)"
```

---

### Task 4: Dashboard styles (`mai.css` append)

**Files:**
- Modify: `mai/mai-data/static/css/mai.css` (append)

- [ ] **Step 1: Append to `mai-data/static/css/mai.css`**

```css

/* ---- dashboard ---- */
.stats{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin:16px 0 18px}
.stat{border:1px solid var(--border);border-radius:8px;padding:12px 14px;background:var(--bg)}
.stat .n{font-size:26px;font-weight:700;line-height:1}
.stat .l{font-size:12px;color:var(--muted);margin-top:4px}
.stat.g .n{color:var(--green)}.stat.y .n{color:var(--amber)}.stat.b .n{color:var(--blue)}

.dash-grid{display:grid;grid-template-columns:1.5fr 1fr;gap:16px}
.panel{border:1px solid var(--border);border-radius:8px;background:var(--bg);overflow:hidden}
.panel-h{padding:9px 14px;border-bottom:1px solid var(--border);background:var(--canvas);font-weight:600;font-size:13px}
.panel-b{padding:14px}

.hm{border-collapse:separate;border-spacing:5px;width:auto}
.hm th{font-size:11px;color:var(--muted);font-weight:600;border:0;background:none;padding:2px}
.hm .hm-l{font-size:11px;color:var(--muted);font-weight:600;text-align:right;border:0;background:none;padding-right:6px}
.hm-c{width:52px;height:38px;border:0;border-radius:6px;text-align:center;font-weight:700;font-size:14px;color:#fff}
.hm-c.na{background:var(--canvas);color:#c4cdd5}
.hm-legend{font-size:11px;color:var(--muted);margin-top:10px}
.hm-grad{display:inline-block;width:120px;height:8px;border-radius:5px;vertical-align:-1px;margin:0 5px;background:linear-gradient(90deg,#2ea043,#d29922,#f85149)}

.brow{display:flex;align-items:center;gap:8px;margin:8px 0;font-size:13px}
.brow .pill{flex:0 0 auto}
.bar{height:10px;border-radius:6px;display:inline-block}
.rfix{padding:6px 0;border-bottom:1px solid var(--surface);font-size:13px}
.rfix:last-child{border-bottom:0}
.core-list{list-style:none;padding:0}.core-list li{padding:4px 0}

@media(max-width:760px){.stats{grid-template-columns:repeat(2,1fr)}.dash-grid{grid-template-columns:1fr}}
```

- [ ] **Step 2: Commit**

```bash
git add mai/mai-data/static/css/mai.css
git commit -m "feat: dashboard styles (stat cards, heatmap cells, area bars)"
```

---

### Task 5: Full suite + build smoke

**Files:** none (integration only).

- [ ] **Step 1: Run the full test suite**

Run: `cd mai && pytest -q`
Expected: PASS (95 passed — 91 prior + 3 dataviz + 1 publish-site).

- [ ] **Step 2: Rebuild + build the dashboard**

If the backfill `mai.db` is present:
```bash
cd mai && rm -rf mai-data/content mai-data/public && python -m mai.cli.__main__ publish && (cd mai-data && hugo --quiet && echo BUILT) && test -f mai-data/data/drift.json && echo "drift.json OK" && grep -o 'hm-c' mai-data/public/index.html | head -1
```
Expected: prints `BUILT`, `drift.json OK`, and `hm-c` (the heatmap cells rendered in the home page). Open `mai-data/public/index.html` — stat cards + colored drift heatmap + top-area bars + recent-fixed list. If `mai.db` lacks data, seed a couple bugs + a `DriftRepository.upsert` first (see `test_dataviz.py`).

- [ ] **Step 3: Confirm clean working tree**

Run: `git status --short`
Expected: clean (generated `mai-data/content`, `public`, `data` are git-ignored).

---

## Self-Review

- **Spec coverage:** Implements spec §9 dashboard design + §10 flat heatmap + §6/§7 the `drift.json`/`areas`-style data export. This is spec §12 **Phase B**. The 3D frequency sheets are Phase C (out of scope here).
- **Deviation (intentional):** the flat heatmap is **server-rendered** (colored HTML table from `drift.json`), not the spec's `heatmap.js` — simpler, zero-JS, offline-first, and cell colors are Python-computed (testable). `dataviz.py` replaces the spec's `dataviz.py` + obviates `heatmap.js`. Noted for the spec.
- **Invariants:** static & offline (no JS) ✓ · data generated not hardcoded (`dataviz.py` from DB) ✓ · graceful when data missing (`{{ with }}` gates + fallback text) ✓ · one CSS token set (reuses `--green/--amber/--blue/--border/...`) ✓.
- **Placeholder scan:** none — every step has runnable code/commands.
- **Type consistency:** `build_drift_matrix` output (`cores`, `rows[].cells[].{self,value,color}`) matches the `index.html` heatmap loop; `build_dashboard` keys (`stats`, `top_areas[].{name,count,color}`, `recently_fixed[].{id,title,core,related,url}`) match the template; `write_dataviz(session, out_dir)` is called by `publish_site(session, out_dir)`.

## Notes for later plans

- **Phase C** (3D): `frequency3d.js` (Three.js, vendored) reading a heightfield JSON from `dataviz.py`; a `sync` section layout mounting it as the hero.
- **Heatmap drill-down:** make a cell link to the per-pair drift page (`/sync/<a>--vs--<b>/`).
- **Verdict bar** panel (open/likely/fixed) like the mockup — small addition to the dashboard once `likely_fixed` is populated (after the embedding-threshold tune).
- **Area QA** (deferred from Phase A) will also improve the Top-areas chart (shrink "Other").
