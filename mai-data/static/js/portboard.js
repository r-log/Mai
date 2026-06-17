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
      applyFilters();
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
    var showDis = !!(fDis && fDis.checked);
    board.classList.toggle('show-dismissed', showDis);
    Array.prototype.forEach.call(board.querySelectorAll('.pcard'), function (card) {
      var ok = (!tier || card.getAttribute('data-tier') === tier)
        && (!src || card.getAttribute('data-source') === src)
        && (!q || card.getAttribute('data-text').indexOf(q) !== -1)
        && (showDis || !card.classList.contains('dismissed'));
      card.style.display = ok ? '' : 'none';
    });
  }
  [fTier, fSrc, fDis].forEach(function (el) { if (el) el.addEventListener('change', applyFilters); });
  if (fSearch) fSearch.addEventListener('input', applyFilters);
})();
