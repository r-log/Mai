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
