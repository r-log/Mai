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
