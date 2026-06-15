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
  // Stack by expansion order: Zero (bottom) → One → Two → Three (top).
  var CORE_ORD = { zero: 0, one: 1, two: 2, three: 3 };
  function coreOrd(c) {
    var n = (c.name || '').toLowerCase(); return CORE_ORD[n] !== undefined ? CORE_ORD[n] : 99;
  }
  CORES.sort(function (a, b) { return coreOrd(a) - coreOrd(b); });
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
      mesh.position.y = (li - (CORES.length - 1) / 2) * GAP; mesh.visible = c._off !== true;
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
  // List rows top→bottom to match the stack (Three at top … Zero at bottom).
  for (var di = CORES.length - 1; di >= 0; di--) {
    (function (i) {
      var c = CORES[i];
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
    })(di);
  }
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
    W = wrap.clientWidth || 1000; H = canvas.clientHeight || 420;
    if (W <= 0 || H <= 0) return;
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
