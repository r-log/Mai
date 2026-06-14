# Web Redesign Phase C — 3D Frequency Sheets Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the signature 3D "frequency sheets" view to the `/sync/` page — one wireframe heightmap surface per core, height = that core's divergence intensity per subsystem, driven by a heightfield JSON Mai generates from `drift_obs`.

**Architecture:** A new `build_frequency()` in `dataviz.py` produces `data/frequency.json` (cores + y-offsets, subsystems + positions, per-core/per-subsystem intensity). The `sync` section layout embeds that JSON into the page as `window.MAI_FREQ` (Hugo `.Site.Data` + `jsonify` — no runtime fetch), loads Three.js from CDN with a graceful fallback, and `frequency3d.js` (vendored static asset) renders the stacked wireframe surfaces with drag-to-rotate.

**Tech Stack:** Python 3.12 · SQLAlchemy 2.0 async · pytest + pytest-asyncio · Hugo (`.Site.Data` + `jsonify`) · Three.js r128 (client-side WebGL).

---

## Builds on Plans 01–10

Reuse as-is (do NOT redefine): `mai.publish.dataviz` (`write_dataviz`, `_short_core` — extended here), `mai.db.models.DriftObservation`, `tests/conftest.py`, the Phase B dashboard. The `/sync/` section currently falls through to `_default/list.html` (plain links); this plan adds a dedicated `sync` layout.

**Design principles (spec §4, §10):** static & offline (data embedded, not fetched); graceful fallback if Three.js/data missing; the 3D is an enhancement layered over the existing per-pair drift list.

## File Structure

```
src/mai/publish/
  dataviz.py                     # MODIFY: add build_frequency + write frequency.json
mai-data/
  layouts/sync/list.html         # NEW: 3D hero (embed data + canvas + scripts) + pair links
  static/js/frequency3d.js       # NEW: Three.js scene reading window.MAI_FREQ
  static/css/mai.css             # MODIFY (append): .freq-hero styles
tests/
  test_dataviz.py                # add build_frequency + write_dataviz(3 files) tests
```

---

### Task 1: Frequency heightfield data

**Files:**
- Modify: `mai/src/mai/publish/dataviz.py` (add `build_frequency`; `write_dataviz` writes `frequency.json`)
- Modify: `mai/tests/test_dataviz.py` (add two tests)

- [ ] **Step 1: Add the failing tests**

Append to `mai/tests/test_dataviz.py`:

```python
async def test_build_frequency_heightfield(session):
    from mai.publish.dataviz import build_frequency
    d = DriftRepository(session)
    await d.upsert("mangoszero/server", "mangostwo/server", "src/game/Object",
                   {"shared": 80, "diverged": 60, "identical": 20, "only_a": 0, "only_b": 0})
    await d.upsert("mangoszero/server", "mangostwo/server", "src/shared",
                   {"shared": 40, "diverged": 10, "identical": 30, "only_a": 0, "only_b": 0})
    await session.commit()
    f = await build_frequency(session)
    assert {c["name"] for c in f["cores"]} == {"Zero", "Two"}
    assert all("y" in c and "full" in c for c in f["cores"])
    names = {s["name"] for s in f["subsystems"]}
    assert "Object" in names and "shared" in names      # last path segment
    assert all("x" in s and "z" in s for s in f["subsystems"])
    zero_full = next(c["full"] for c in f["cores"] if c["name"] == "Zero")
    assert f["intensity"][zero_full]["Object"] > 0      # 60/80 -> positive height


async def test_write_dataviz_writes_three_files(session, tmp_path):
    from mai.publish.dataviz import write_dataviz
    await DriftRepository(session).upsert(
        "mangoszero/server", "mangostwo/server", "src/game/Object",
        {"shared": 10, "diverged": 5, "identical": 5, "only_a": 0, "only_b": 0})
    await session.commit()
    await write_dataviz(session, str(tmp_path))
    for name in ("drift.json", "dashboard.json", "frequency.json"):
        assert (tmp_path / "data" / name).exists()
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd mai && pytest tests/test_dataviz.py::test_build_frequency_heightfield -v`
Expected: FAIL with `ImportError: cannot import name 'build_frequency'`

- [ ] **Step 3: Add `build_frequency` + update `write_dataviz` in `dataviz.py`**

Add `import math` at the top of `mai/src/mai/publish/dataviz.py`. Add this function (e.g. after `build_dashboard`):

```python
async def build_frequency(session: AsyncSession, top_n: int = 6) -> dict:
    """Per-core, per-subsystem divergence intensity as a stacked heightfield."""
    obs = list(await session.scalars(select(DriftObservation)))
    if not obs:
        return {"cores": [], "subsystems": [], "intensity": {}, "max": 1.6}
    forks = sorted({o.fork_a for o in obs} | {o.fork_b for o in obs})

    shared_by_sub: dict[str, int] = {}
    for o in obs:
        shared_by_sub[o.subsystem] = shared_by_sub.get(o.subsystem, 0) + o.shared
    top = [s for s, _ in sorted(shared_by_sub.items(), key=lambda kv: kv[1], reverse=True)[:top_n]]

    subsystems = []
    for i, full in enumerate(top):
        ang = 2 * math.pi * i / max(1, len(top))
        subsystems.append({"name": full.split("/")[-1], "full": full,
                           "x": round(4.5 * math.cos(ang), 2), "z": round(4.5 * math.sin(ang), 2)})

    intensity: dict[str, dict] = {}
    for fork in forks:
        per_sub = {}
        for sub in subsystems:
            vals = [o.diverged / o.shared for o in obs
                    if o.subsystem == sub["full"] and o.shared
                    and fork in (o.fork_a, o.fork_b)]
            if vals:
                per_sub[sub["name"]] = round(sum(vals) / len(vals) * 1.5, 3)
        intensity[fork] = per_sub

    spacing, n = 2.4, len(forks)
    cores = [{"name": _short_core(f), "full": f,
              "y": round((n - 1) / 2 * spacing - i * spacing, 2)}
             for i, f in enumerate(forks)]
    return {"cores": cores, "subsystems": subsystems, "intensity": intensity, "max": 1.6}
```

Then in `write_dataviz`, add the frequency write (after the dashboard write):
```python
    (data / "frequency.json").write_text(
        json.dumps(await build_frequency(session), indent=2), encoding="utf-8")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd mai && pytest tests/test_dataviz.py -v`
Expected: PASS (5 passed — 3 prior + 2 new).

- [ ] **Step 5: Commit**

```bash
git add mai/src/mai/publish/dataviz.py mai/tests/test_dataviz.py
git commit -m "feat: build_frequency heightfield + write frequency.json"
```

---

### Task 2: The 3D renderer (`frequency3d.js`)

**Files:**
- Create: `mai/mai-data/static/js/frequency3d.js`

(Static asset — no unit test; verified by the build smoke in Task 5 and visually in the browser.)

- [ ] **Step 1: Write `mai-data/static/js/frequency3d.js`**

```javascript
(function () {
  var F = window.MAI_FREQ;
  var err = document.getElementById('freq-err');
  if (!window.THREE) { if (err) err.textContent = '3D library failed to load.'; return; }
  if (!F || !F.cores || !F.cores.length) {
    if (err) err.textContent = 'No drift data for the 3D view yet — run `mai drift`.'; return;
  }
  var wrap = document.getElementById('freq');
  var canvas = document.getElementById('freq-c');
  if (!wrap || !canvas) return;

  var W = wrap.clientWidth, H = wrap.clientHeight || 520;
  var renderer = new THREE.WebGLRenderer({ canvas: canvas, antialias: true, alpha: true });
  renderer.setPixelRatio(window.devicePixelRatio || 1); renderer.setSize(W, H);
  var scene = new THREE.Scene();
  var camera = new THREE.PerspectiveCamera(45, W / H, 0.1, 200);
  camera.position.set(0, 4, 22); camera.lookAt(0, 0, 0);
  var group = new THREE.Group(); scene.add(group);

  var subs = F.subsystems, MAX = F.max || 1.6, SPR = 1.5;
  function field(x, z, full) {
    var amp = F.intensity[full] || {}, h = 0;
    for (var i = 0; i < subs.length; i++) {
      var s = subs[i], dx = x - s.x, dz = z - s.z;
      h += (amp[s.name] || 0) * Math.exp(-(dx * dx + dz * dz) / (2 * SPR * SPR));
    }
    return h + 0.1 * Math.sin(x * 1.25) * Math.cos(z * 1.05);
  }
  function heatRGB(t) { t = Math.max(0, Math.min(1, t)); return [Math.min(1, t * 1.7), Math.min(1, (1 - t) * 1.7), 0.2]; }
  function label(txt) {
    var cv = document.createElement('canvas'); cv.width = 256; cv.height = 64;
    var ctx = cv.getContext('2d'); ctx.fillStyle = '#e6edf3'; ctx.font = 'bold 44px sans-serif'; ctx.fillText(txt, 8, 48);
    var sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: new THREE.CanvasTexture(cv), transparent: true, depthWrite: false }));
    sp.scale.set(3.2, 0.8, 1); return sp;
  }

  F.cores.forEach(function (c) {
    var SEG = 46, geo = new THREE.PlaneGeometry(15, 15, SEG, SEG), pos = geo.attributes.position, cols = [];
    for (var i = 0; i < pos.count; i++) {
      var x = pos.getX(i), zz = pos.getY(i), h = field(x, zz, c.full);
      pos.setXYZ(i, x, h, zz);
      var rgb = heatRGB(h / MAX); cols.push(rgb[0], rgb[1], rgb[2]);
    }
    geo.setAttribute('color', new THREE.Float32BufferAttribute(cols, 3));
    geo.computeVertexNormals();
    var mesh = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ vertexColors: true, wireframe: true, transparent: true, opacity: 0.9 }));
    mesh.position.y = c.y; group.add(mesh);
    var lb = label(c.name); lb.position.set(-8.6, c.y + 0.55, -7.6); group.add(lb);
  });
  subs.forEach(function (s) {
    var top = F.cores[0].y + 1.8, bot = F.cores[F.cores.length - 1].y - 0.4;
    var g = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(s.x, bot, s.z), new THREE.Vector3(s.x, top, s.z)]);
    group.add(new THREE.Line(g, new THREE.LineBasicMaterial({ color: 0x30363d, transparent: true, opacity: 0.4 })));
  });

  var drag = false, px = 0, py = 0, ry = 0.45, rx = 0.15, auto = true;
  canvas.addEventListener('mousedown', function (e) { drag = true; auto = false; px = e.clientX; py = e.clientY; });
  window.addEventListener('mouseup', function () { drag = false; });
  window.addEventListener('mousemove', function (e) {
    if (!drag) return;
    ry += (e.clientX - px) * 0.008; rx += (e.clientY - py) * 0.008;
    rx = Math.max(-1.2, Math.min(1.2, rx)); px = e.clientX; py = e.clientY;
  });
  window.addEventListener('resize', function () {
    W = wrap.clientWidth; H = wrap.clientHeight || 520;
    renderer.setSize(W, H); camera.aspect = W / H; camera.updateProjectionMatrix();
  });
  function animate() {
    requestAnimationFrame(animate);
    if (auto) ry += 0.003;
    group.rotation.y = ry; group.rotation.x = rx;
    renderer.render(scene, camera);
  }
  animate();
})();
```

- [ ] **Step 2: Commit**

```bash
git add mai/mai-data/static/js/frequency3d.js
git commit -m "feat: frequency3d.js (Three.js stacked heightmap surfaces)"
```

---

### Task 3: The `sync` section layout

**Files:**
- Create: `mai/mai-data/layouts/sync/list.html`

- [ ] **Step 1: Write `mai-data/layouts/sync/list.html`**

```html
{{ define "main" }}
  <h1>Cross-core drift</h1>
  <p class="muted">Each layer is a core; the surface rises where that core diverges most per subsystem. Drag to rotate.</p>

  <div id="freq" class="freq-hero">
    <canvas id="freq-c"></canvas>
    <div id="freq-err" class="freq-err"></div>
    <div class="freq-legend">low <span class="hm-grad"></span> high divergence · drag to rotate</div>
  </div>

  <script>window.MAI_FREQ = {{ with .Site.Data.frequency }}{{ . | jsonify }}{{ else }}null{{ end }};</script>
  <script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
  <script src="/js/frequency3d.js"></script>

  <h2 style="margin-top:22px">Per-pair drift</h2>
  <ul class="core-list">
    {{ range .Pages }}<li><a href="{{ .RelPermalink }}">{{ .Title }}</a></li>{{ end }}
  </ul>
{{ end }}
```

- [ ] **Step 2: Commit**

```bash
git add mai/mai-data/layouts/sync/list.html
git commit -m "feat: sync section layout mounting the 3D frequency-sheet hero"
```

---

### Task 4: Hero styles (`mai.css` append)

**Files:**
- Modify: `mai/mai-data/static/css/mai.css` (append)

- [ ] **Step 1: Append to `mai-data/static/css/mai.css`**

```css

/* ---- 3D frequency hero ---- */
.freq-hero{position:relative;height:520px;border-radius:12px;overflow:hidden;background:#0a0e14;margin:14px 0;box-shadow:0 8px 30px #0003}
#freq-c{width:100%;height:100%;display:block;cursor:grab}
#freq-c:active{cursor:grabbing}
.freq-err{position:absolute;top:48%;left:0;right:0;text-align:center;color:#f85149;font-size:13px;pointer-events:none}
.freq-legend{position:absolute;bottom:12px;left:14px;font-size:11px;color:#7d8590;pointer-events:none}
```

- [ ] **Step 2: Commit**

```bash
git add mai/mai-data/static/css/mai.css
git commit -m "feat: 3D frequency hero styles"
```

---

### Task 5: Full suite + build smoke

**Files:** none (integration only).

- [ ] **Step 1: Run the full test suite**

Run: `cd mai && pytest -q`
Expected: PASS (98 passed — 96 prior + 2 new dataviz tests).

- [ ] **Step 2: Rebuild + build, verify the 3D page wiring**

If the backfill `mai.db` is present:
```bash
cd mai && rm -rf mai-data/content mai-data/public mai-data/data && python -m mai.cli.__main__ publish && (cd mai-data && hugo --quiet && echo BUILT) && test -f mai-data/data/frequency.json && echo "frequency.json OK" && grep -c 'MAI_FREQ\|freq-c\|frequency3d.js' mai-data/public/sync/index.html
```
Expected: prints `BUILT`, `frequency.json OK`, and a non-zero count (the `/sync/` page embeds `window.MAI_FREQ`, the `#freq-c` canvas, and the `frequency3d.js` script). The `static/js/frequency3d.js` is copied to `public/js/frequency3d.js` by Hugo — confirm `test -f mai-data/public/js/frequency3d.js`. (If `mai.db` lacks drift data, seed via `DriftRepository.upsert` first; with no data the page still builds and shows the "run `mai drift`" fallback.)

- [ ] **Step 3: Confirm clean working tree**

Run: `git status --short`
Expected: clean (generated `mai-data/content`, `data`, `public` are git-ignored; `static/js/frequency3d.js` is tracked).

---

## Self-Review

- **Spec coverage:** Implements spec §10 (the 3D frequency-sheet visualization) + §12 **Phase C**. Data comes from `drift_obs` via `build_frequency` (spec §6/§7 data-export pattern). Completes the web-design spec (Phases A+B+C all built).
- **Deviation (intentional, noted):** Three.js loads from **CDN with a graceful fallback** (the demo-proven approach), not vendored — spec §13 #3 leaned "vendor it"; vendoring (committing the ~600 KB lib) is deferred as hardening. The data is **embedded** (`window.MAI_FREQ` via `.Site.Data` + `jsonify`), not fetched — strictly better for offline/Access than the spec's implied fetch.
- **Invariants:** static & offline (data embedded; page builds without JS — the 3D is additive) ✓ · graceful fallback (`#freq-err` messages when THREE or data is missing) ✓ · data generated not hardcoded (`build_frequency` from DB) ✓ · one CSS token set ✓.
- **Placeholder scan:** none — every step has runnable code/commands.
- **Type consistency:** `build_frequency` output (`cores[].{name,full,y}`, `subsystems[].{name,full,x,z}`, `intensity[full][name]`, `max`) matches exactly what `frequency3d.js` reads (`F.cores`, `F.subsystems`, `F.intensity[c.full][s.name]`, `F.max`); `write_dataviz` now writes three files; `_short_core` reused for core display names.

## Notes for later plans

- **Vendor Three.js:** download `three.min.js` to `static/js/` and switch the `<script src>` to `/js/three.min.js` for full offline/Access independence.
- **Heatmap ↔ 3D cross-link:** clicking a flat-heatmap cell could deep-link to the 3D view focused on that pair.
- **Intensity metric:** currently avg cross-pair divergence per core/subsystem; could blend in per-core bug counts per area once area↔subsystem mapping is firmed up.
- **Web design spec is now fully implemented** (Phases A/B/C) — update `docs/specs/web-design.md` status to Approved/Built.
```
