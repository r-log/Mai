(function () {
  var board = document.getElementById('port-board');
  var summary = document.getElementById('port-summary');
  var fresh = document.getElementById('port-fresh');
  var TIER = { surgical:'#1a7f37', small:'#9a6700', moderate:'#bc4c00', bulk:'#cf222e' };
  var STATUSES = ['claimed', 'in_progress', 'pr_linked'];
  var data = null, me = null, csrf = '', view = 'all';

  function esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c]; });
  }
  function toast(msg) {
    var t = document.createElement('div'); t.className = 'toast'; t.textContent = msg;
    document.body.appendChild(t); setTimeout(function () { t.remove(); }, 2600);
  }

  function load() {
    fetch('/api/board', { headers: { 'Accept': 'application/json' } })
      .then(function (r) {
        if (r.status === 303 || r.redirected) { location = '/login'; return null; }
        return r.json();
      })
      .then(function (j) { if (!j) return; data = j; me = j.me; csrf = j.csrf;
        renderSummary(); renderFilters(); render(); })
      .catch(function () { toast('could not load board'); });
  }

  function renderSummary() {
    if (summary && data.summary) {
      var t = data.summary.tiers || {};
      summary.textContent = (data.summary.total || 0) + ' open · surgical ' + (t.surgical || 0)
        + ' · small ' + (t.small || 0) + ' · moderate ' + (t.moderate || 0)
        + ' · bulk ' + (t.bulk || 0);
    }
    if (fresh) fresh.textContent = 'as of now';
  }

  // gather all candidates (with column core) into a flat list
  function allCands() {
    var out = [];
    (data.columns || []).forEach(function (col) {
      (col.candidates || []).forEach(function (c) { out.push(c); });
    });
    return out;
  }

  function renderFilters() {
    var fSrc = document.getElementById('f-source'), fSub = document.getElementById('f-subsystem');
    var src = {}, sub = {};
    allCands().forEach(function (c) { src[c.source_core] = 1; sub[c.subsystem] = 1; });
    function fill(sel, vals) {
      if (!sel) return;
      Object.keys(vals).sort().forEach(function (v) {
        var o = document.createElement('option'); o.value = v; o.textContent = v; sel.appendChild(o);
      });
    }
    if (fSrc && fSrc.options.length <= 1) fill(fSrc, src);
    if (fSub && fSub.options.length <= 1) fill(fSub, sub);
  }

  function overlay(c) { return c.board || null; }
  function assigneeOf(c) { var b = overlay(c); return b ? b.assignee : null; }
  function statusOf(c) { var b = overlay(c); return b ? b.status : 'open'; }

  function cardHTML(c) {
    // TIER maps to a hard-coded hex (or fallback); c.tier itself is never interpolated into CSS
    var dot = '<span class="tdot" style="background:' + (TIER[c.tier] || '#59636e') + '"></span>';
    var safeUrl = /^https?:\/\//.test(c.source_url || '') ? c.source_url : '';
    var link = safeUrl
      ? '<a class="src-link" href="' + esc(safeUrl) + '" target="_blank" rel="noopener">↗</a>' : '';
    var who = assigneeOf(c), st = statusOf(c);
    var chip = who
      ? '<span class="chip ' + (who === (me && me.username) ? 'mine' : '') + '">' + esc(who) + '</span>'
      : '<button data-act="claim">claim</button>';
    var pill = (st && st !== 'open') ? '<span class="pill ' + esc(st) + '">' + esc(st.replace('_', ' ')) + '</span>' : '';
    var mineOrMaint = (who && who === (me && me.username)) || (me && me.is_maintainer);
    var statusSel = '';
    if (mineOrMaint && who) {
      statusSel = '<select data-act="status"><option value="">status…</option>'
        + STATUSES.map(function (s) {
            return '<option value="' + s + '"' + (s === st ? ' selected' : '') + '>'
              + s.replace('_', ' ') + '</option>'; }).join('') + '</select>'
        + '<button data-act="link_pr">link PR</button>'
        + '<button data-act="unassign">release</button>';
    }
    var maint = (me && me.is_maintainer)
      ? '<button data-act="assign">assign…</button>'
        + (st === 'dismissed' ? '<button data-act="restore">restore</button>'
                              : '<button data-act="dismiss">dismiss</button>') : '';
    var cls = 'pcard' + (st === 'dismissed' ? ' dismissed' : '');
    return '<article class="' + cls + '" data-id="' + esc(c.id) + '" data-tier="' + esc(c.tier)
      + '" data-source="' + esc(c.source_core) + '" data-subsystem="' + esc(c.subsystem)
      + '" data-assignee="' + esc(who || '') + '" data-text="'
      + esc((c.title + ' ' + c.subsystem).toLowerCase()) + '">'
      + '<div class="pc-top">' + dot + '<span class="pc-from">from ' + esc(c.source_core) + '</span>' + link + '</div>'
      + '<div class="pc-title">' + esc(c.title) + '</div>'
      + '<div class="pc-meta">' + esc(c.subsystem) + ' · ' + esc(c.magnitude) + ' lines</div>'
      + '<div class="pc-row">' + chip + pill + '</div>'
      + '<ul class="pc-evidence" hidden>' + (c.evidence || []).map(function (e) {
          return '<li>' + esc(e) + '</li>'; }).join('') + '</ul>'
      + '<div class="pc-actions">' + statusSel + maint + '</div></article>';
  }

  function columnsForView() {
    if (view === 'person') {
      var byPerson = {};
      allCands().forEach(function (c) {
        var who = assigneeOf(c) || '(unassigned)';
        (byPerson[who] = byPerson[who] || []).push(c);
      });
      return Object.keys(byPerson).sort().map(function (p) {
        return { core: p, label: p, candidates: byPerson[p], count: byPerson[p].length }; });
    }
    return (data.columns || []).map(function (col) {
      var cands = col.candidates;
      if (view === 'mine') cands = cands.filter(function (c) {
        return assigneeOf(c) === (me && me.username); });
      return { core: col.core, label: 'Port into ' + col.core.toUpperCase(),
               candidates: cands, count: cands.length };
    });
  }

  function render() {
    var cols = columnsForView();
    board.innerHTML = cols.map(function (col) {
      var cards = col.candidates.length
        ? col.candidates.map(cardHTML).join('')
        : '<div class="empty-state">nothing here</div>';
      return '<section class="pcol" data-core="' + esc(col.core) + '">'
        + '<div class="pcol-h"><span>' + esc(col.label) + '</span>'
        + '<span class="pcol-ct">' + col.count + '</span></div>'
        + '<div class="pcol-cards">' + cards + '</div></section>';
    }).join('');
    applyFilters();
  }

  function findCand(id) {
    var hit = null;
    allCands().forEach(function (c) { if (c.id === id) hit = c; });
    return hit;
  }

  function mutate(id, action, payload) {
    var body = Object.assign({ csrf: csrf }, payload || {});
    fetch('/api/board/' + encodeURIComponent(id) + '/' + action, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
    }).then(function (r) {
      return r.json().then(function (j) { return { status: r.status, body: j }; });
    }).then(function (res) {
      if (res.status === 200) {
        var c = findCand(id);
        if (c) c.board = res.body;   // overlay shape matches _overlay()
        render();
      } else if (res.status === 409) {
        toast('already claimed by ' + (res.body.assignee || 'someone'));
        load();
      } else if (res.status === 403) {
        toast('not allowed');
      } else {
        toast((res.body && res.body.error) || 'action failed');
      }
    }).catch(function () { toast('network error'); });
  }

  // --- interactions ---
  board.addEventListener('click', function (e) {
    var card = e.target.closest('.pcard');
    if (!card) return;
    var id = card.getAttribute('data-id');
    var btn = e.target.closest('[data-act]');
    if (btn && btn.tagName === 'BUTTON') {
      e.stopPropagation();
      var act = btn.getAttribute('data-act');
      if (act === 'assign') {
        var who = prompt('assign to which username?'); if (who) mutate(id, 'assign', { value: who });
      } else if (act === 'dismiss') {
        var why = prompt('dismiss reason (why this is not a port)?'); if (why) mutate(id, 'dismiss', { reason: why });
      } else if (act === 'link_pr') {
        var url = prompt('PR url?'); if (url) mutate(id, 'link_pr', { related_pr: url });
      } else { mutate(id, act, {}); }
      return;
    }
    if (e.target.classList.contains('pc-title')) {
      var ev = card.querySelector('.pc-evidence'); if (ev) ev.hidden = !ev.hidden;
    }
  });
  board.addEventListener('change', function (e) {
    var sel = e.target.closest('select[data-act="status"]');
    if (!sel || !sel.value) return;
    var card = e.target.closest('.pcard');
    mutate(card.getAttribute('data-id'), 'status', { value: sel.value });
  });

  // --- views ---
  var views = document.getElementById('port-views');
  if (views) views.addEventListener('click', function (e) {
    var b = e.target.closest('button[data-view]'); if (!b) return;
    view = b.getAttribute('data-view');
    Array.prototype.forEach.call(views.querySelectorAll('button'), function (x) {
      x.classList.toggle('on', x === b); });
    render();
  });

  // --- filters ---
  var fTier = document.getElementById('f-tier'), fSrc = document.getElementById('f-source');
  var fSub = document.getElementById('f-subsystem'), fSearch = document.getElementById('f-search');
  var fDis = document.getElementById('f-dismissed');
  function applyFilters() {
    var tier = fTier ? fTier.value : '', src = fSrc ? fSrc.value : '';
    var sub = fSub ? fSub.value : '', q = fSearch ? fSearch.value.trim().toLowerCase() : '';
    var showDis = !!(fDis && fDis.checked);
    board.classList.toggle('show-dismissed', showDis);
    Array.prototype.forEach.call(board.querySelectorAll('.pcard'), function (card) {
      var ok = (!tier || card.getAttribute('data-tier') === tier)
        && (!src || card.getAttribute('data-source') === src)
        && (!sub || card.getAttribute('data-subsystem') === sub)
        && (!q || card.getAttribute('data-text').indexOf(q) !== -1)
        && (showDis || !card.classList.contains('dismissed'));
      card.style.display = ok ? '' : 'none';
    });
  }
  [fTier, fSrc, fSub, fDis].forEach(function (el) { if (el) el.addEventListener('change', applyFilters); });
  if (fSearch) fSearch.addEventListener('input', applyFilters);

  load();
})();
