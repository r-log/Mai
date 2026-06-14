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
