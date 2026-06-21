# Board Phase B3 — The /port/ UI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Draw the collaborative board: a `/port/` page served behind the login gate that fetches the live `/api/board`, renders the target-fork columns with shared assignment/status, and lets users claim/assign/dismiss — the cockpit Antz asked for, on screen.

**Architecture:** The FastAPI app gains a gated `GET /port` HTML shell and a `/static` mount (`board.css`, `portboard.js`); `/` redirects to `/port`. The JS fetches `GET /api/board` (the B2 endpoint: engine candidates overlaid with `BoardItem` state + `csrf` + `me`), renders columns/cards reusing the v1 visual language, offers **All cores · My ports · By person** views + tier/subsystem/source/search filters, and wires the mutation buttons to `POST /api/board/{id}/{action}` (CSRF-protected). It is the live, shared successor to the v1 static `portboard.js` (which read a JSON snapshot + localStorage).

**Tech Stack:** FastAPI + `StaticFiles`, vanilla JS (no framework, no build step), CSS adapted from the existing `mai-data/static/css/mai.css` port-board block. Server routes are pytest-tested; the JS is verified by a delivery test + an HTTP-level end-to-end smoke + a manual browser check (the project has no JS test harness — same as the v1 `portboard.js`).

## Global Constraints

- **Login is the gate (B1).** `/port` is session-gated (not in `_PUBLIC`); `/static/*` is allowed without a session (it's code/styling, not data — the data is behind the gated `/api/board`).
- **Engine owns truth; board owns intent.** The UI only reads `/api/board` and POSTs board mutations; it never asserts a fix is "ported".
- **CSRF on every mutation:** the JS reads `csrf` from the `/api/board` payload and sends it in the JSON body of each `POST` (the B2 contract).
- **Authorization is server-side (B2).** The UI hides maintainer-only controls (assign/dismiss/restore) when `me.is_maintainer` is false, but the server is the real gate — never trust the client.
- **Reuse the v1 look:** match the existing `.pcol`/`.pcard`/`.tdot`/`.pc-top`/`.pc-from`/`.pc-title`/`.pc-meta`/`.pc-evidence` structure and colors from `mai-data/static/css/mai.css`.
- **Escape all interpolated values** in the JS (`esc()` helper, as in v1) — no `innerHTML` with raw API strings.
- 4-space indent in Python; 2-space in JS/CSS to match the existing static files. `feat:`/`test:` commits, **NO AI attribution**. Commit with `git -c user.name="r-log" commit -m "..."`.
- The `/api/board` response shape (from B2): `{ "summary": {total, tiers}, "columns": [{core, repo, count, candidates: [{id, title, source_core, source_url, subsystem, tier, magnitude, confidence, patch_id, evidence, board}]}], "_orphans": [...], "csrf": str, "me": {username, is_maintainer} }`. Each `candidate.board` is `null` or `{assignee, status, related_pr, dismissed, dismiss_reason}`.

---

## File Structure

| File | Responsibility |
|------|----------------|
| `src/mai/web/app.py` (modify) | Add `GET /port` (gated HTML shell), `/static` mount, `/` → `/port` redirect, `_port_html()` helper. |
| `src/mai/web/static/board.css` | Board styling (adapted from v1 mai.css + assignee/status/view-toggle additions). |
| `src/mai/web/static/portboard.js` | Live board: fetch, render, views, filters, mutations. |
| `tests/test_port_page.py` | Server tests: gate, shell markers, static served, `/`→`/port`. |
| `tests/test_port_e2e.py` | HTTP-level end-to-end: login→set-password→GET /port→GET /api/board→POST claim with CSRF→overlay reflects it. |

---

## Task 1: /port route + static mount + redirect

**Files:**
- Modify: `src/mai/web/app.py`
- Create: `src/mai/web/static/board.css` (placeholder content this task; full styling in Task 2)
- Create: `src/mai/web/static/portboard.js` (placeholder content this task; full app in Task 3)
- Test: `tests/test_port_page.py`

**Interfaces:**
- Consumes: B1 `create_app` + gate.
- Produces: `GET /port` (gated) returns the board shell HTML; `app.mount("/static", StaticFiles(...))`; `GET /` → 303 `/port`; `_port_html(username, is_maintainer)` helper.

- [ ] **Step 1: Write the failing test**

Create `tests/test_port_page.py`:

```python
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mai.auth.accounts import create_account
from mai.auth.fake import FakeHasher
from mai.db.base import Base
from mai.web.app import create_app


@pytest_asyncio.fixture
async def client_pw():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    hasher = FakeHasher()
    async with factory() as s:
        pw = await create_account(s, hasher, "antz", is_maintainer=True)
        await s.commit()
    app = create_app(factory, hasher, "test-secret", cookie_secure=False)
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test", follow_redirects=False) as ac:
        yield ac, pw


async def _login(ac, pw):
    await ac.post("/login", data={"username": "antz", "password": pw})
    await ac.post("/set-password", data={"new_password": "a-good-long-password"})


async def test_port_requires_session(client_pw):
    ac, _ = client_pw
    r = await ac.get("/port")
    assert r.status_code == 303
    assert r.headers["location"] == "/login"


async def test_root_redirects_to_port(client_pw):
    ac, pw = client_pw
    await _login(ac, pw)
    r = await ac.get("/")
    assert r.status_code == 303
    assert r.headers["location"] == "/port"


async def test_port_shell_has_mount_points(client_pw):
    ac, pw = client_pw
    await _login(ac, pw)
    r = await ac.get("/port")
    assert r.status_code == 200
    body = r.text
    for marker in ['id="port-board"', 'id="port-summary"', 'id="port-views"',
                   'id="port-filters"', '/static/portboard.js', '/static/board.css']:
        assert marker in body


async def test_static_assets_served(client_pw):
    ac, pw = client_pw
    await _login(ac, pw)  # static is public, but logging in is harmless
    css = await ac.get("/static/board.css")
    js = await ac.get("/static/portboard.js")
    assert css.status_code == 200
    assert js.status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_port_page.py -v`
Expected: FAIL (no `/port` route / no static mount yet).

- [ ] **Step 3: Create placeholder static files**

Create `src/mai/web/static/board.css` with one line (real styles land in Task 2):

```css
/* mai port-debt board styles (filled in Task 2) */
```

Create `src/mai/web/static/portboard.js` with one line (real app lands in Task 3):

```javascript
/* mai port board app (filled in Task 3) */
```

- [ ] **Step 4: Add the shell helper, route, mount, and redirect**

In `src/mai/web/app.py`, add these imports near the top:

```python
from pathlib import Path

from fastapi.staticfiles import StaticFiles
```

Add a `_port_html` helper next to the other `_*_html` helpers:

```python
def _port_html(username: str, is_maintainer: bool) -> str:
    role = "maintainer" if is_maintainer else "member"
    return _page("Mai — Port Debt", f"""
        <link rel='stylesheet' href='/static/board.css'>
        <header class='port-head'>
          <h1>Port Debt</h1>
          <span id='port-summary' class='port-summary'></span>
          <span id='port-fresh' class='port-fresh'></span>
          <span class='port-me'>{html.escape(username)} · {role}
            <a href='/logout' onclick="event.preventDefault();
               fetch('/logout',{{method:'POST'}}).then(()=>location='/login')">log out</a>
          </span>
        </header>
        <nav id='port-views' class='port-views'>
          <button data-view='all' class='on'>All cores</button>
          <button data-view='mine'>My ports</button>
          <button data-view='person'>By person</button>
        </nav>
        <div id='port-filters' class='port-filters'>
          <select id='f-tier'><option value=''>all tiers</option>
            <option>surgical</option><option>small</option>
            <option>moderate</option><option>bulk</option></select>
          <select id='f-source'><option value=''>all sources</option></select>
          <select id='f-subsystem'><option value=''>all subsystems</option></select>
          <input id='f-search' placeholder='search title/subsystem'>
          <label><input type='checkbox' id='f-dismissed'> show dismissed</label>
        </div>
        <div id='port-board' class='port-board'></div>
        <script src='/static/portboard.js'></script>""")
```

In `create_app`, mount static (before `return app`) — resolve the directory relative to this file:

```python
    app.mount("/static",
              StaticFiles(directory=Path(__file__).parent / "static"),
              name="static")
```

Add the `/static` prefix to the gate allowlist check is NOT needed — the gate already lets `path.startswith("/static")` through.

Change the home route to redirect, and add the `/port` route:

```python
    @app.get("/")
    async def home():
        return RedirectResponse("/port", status_code=303)

    @app.get("/port", response_class=HTMLResponse)
    async def port(request: Request):
        username = request.session["username"]
        async with session_factory() as session:
            user = await UserRepository(session).get(username)
        return _port_html(username, bool(user and user.is_maintainer))
```

(Remove the old `_home_html`-based `GET /` body; `_home_html` may stay unused or be deleted — delete it and its test if you remove it, otherwise leave it. Simplest: replace the `GET /` handler body as above and leave `_home_html` defined but unused is NOT allowed by review — so DELETE `_home_html` and remove `tests/test_web_login.py::test_home_html_escapes_username` if present, or repoint that test. Check `tests/test_web_login.py` for any test that GETs `/` expecting the old home body and update it to expect the 303 → `/port`.)

- [ ] **Step 5: Reconcile existing home-page tests**

In `tests/test_web_login.py`, any test that asserts `GET /` returns 200 with "signed in as" must change to expect `303` → `/port`. Specifically update `test_set_password_clears_flag_and_unlocks_board` (it GETs `/` after set-password): change its tail to
```python
    home = await ac.get("/")
    assert home.status_code == 303
    assert home.headers["location"] == "/port"
```
and remove `test_home_html_escapes_username` (the `_home_html` helper is deleted). Run `python -m pytest tests/test_web_login.py -v` to confirm green.

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_port_page.py tests/test_web_login.py -v`
Expected: PASS (port-page tests + adjusted login tests).

- [ ] **Step 7: Run the full suite**

Run: `python -m pytest -q`
Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add src/mai/web/app.py src/mai/web/static/board.css src/mai/web/static/portboard.js tests/test_port_page.py tests/test_web_login.py
git -c user.name="r-log" commit -m "feat: gated /port board shell + static mount + root redirect"
```

---

## Task 2: board.css (visual styling)

**Files:**
- Modify: `src/mai/web/static/board.css`

**Interfaces:** none (pure styling; delivery already covered by Task 1's `test_static_assets_served`).

- [ ] **Step 1: Write the full stylesheet**

Replace `src/mai/web/static/board.css` with (adapted from the v1 `mai.css` port-board block, plus assignee/status/view-toggle classes):

```css
:root { --muted:#59636e; --line:#d0d7de; --accent:#0969da; }
* { box-sizing:border-box; }
body { font:14px/1.5 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;
  margin:0; padding:16px 20px; color:#1f2328; background:#fff; }
a { color:var(--accent); }

.port-head { display:flex; align-items:baseline; gap:12px; flex-wrap:wrap; margin:0 0 8px; }
.port-head h1 { font-size:20px; margin:0; }
.port-summary { font-size:13px; color:var(--muted); }
.port-fresh { font-size:12px; color:var(--muted); }
.port-me { margin-left:auto; font-size:12px; color:var(--muted); }

.port-views { display:flex; gap:6px; margin:8px 0; }
.port-views button { font-size:13px; padding:4px 12px; border:1px solid var(--line);
  border-radius:6px; background:#fff; cursor:pointer; }
.port-views button.on { background:var(--accent); color:#fff; border-color:var(--accent); }

.port-filters { display:flex; gap:8px; align-items:center; margin:10px 0; flex-wrap:wrap; }
.port-filters select, .port-filters input { padding:4px 8px; border:1px solid var(--line);
  border-radius:6px; font-size:13px; }

.port-board { display:grid; grid-template-columns:repeat(4,1fr); gap:12px; align-items:start; }
.pcol { background:#f6f8fa; border:1px solid var(--line); border-radius:8px; min-height:80px; }
.pcol-h { display:flex; justify-content:space-between; align-items:center; padding:8px 10px;
  border-bottom:1px solid var(--line); font-weight:600; }
.pcol-ct { background:#eaeef2; border-radius:20px; padding:0 8px; font-size:12px; font-weight:600; }
.pcol-cards { padding:8px; display:flex; flex-direction:column; gap:8px; }
.empty-state { color:var(--muted); font-size:12px; font-style:italic; text-align:center; padding:20px 8px; }

.pcard { background:#fff; border:1px solid var(--line); border-radius:6px; padding:8px; }
.pc-top { display:flex; align-items:center; gap:6px; font-size:12px; color:var(--muted); }
.tdot { width:9px; height:9px; border-radius:50%; display:inline-block; }
.pc-from { flex:1; }
.src-link { text-decoration:none; font-size:14px; }
.pc-title { font-weight:600; font-size:13px; margin:3px 0; cursor:pointer; }
.pc-meta { font-size:12px; color:var(--muted); }
.pc-evidence { margin:6px 0 0; padding-left:16px; font-size:11px; color:var(--muted); }

.pc-row { display:flex; align-items:center; gap:6px; margin-top:6px; flex-wrap:wrap; }
.chip { font-size:11px; padding:1px 7px; border-radius:20px; background:#eaeef2; color:#1f2328; }
.chip.mine { background:#ddf4ff; color:#0a3069; }
.pill { font-size:11px; padding:1px 7px; border-radius:5px; border:1px solid var(--line); color:var(--muted); }
.pill.claimed { color:#0a3069; border-color:#54aeff; }
.pill.in_progress { color:#9a6700; border-color:#d4a72c; }
.pill.pr_linked { color:#1a7f37; border-color:#4ac26b; }
.pill.dismissed { color:#cf222e; border-color:#ff8182; }

.pc-actions { display:flex; gap:6px; margin-top:6px; flex-wrap:wrap; }
.pc-actions button, .pc-actions select { font-size:11px; padding:2px 6px; border:1px solid var(--line);
  border-radius:5px; background:#fff; cursor:pointer; }
.pcard.dismissed { display:none; }
.port-board.show-dismissed .pcard.dismissed { display:block; opacity:.5; }
.toast { position:fixed; bottom:16px; left:50%; transform:translateX(-50%);
  background:#1f2328; color:#fff; padding:8px 14px; border-radius:6px; font-size:13px; z-index:10; }
```

- [ ] **Step 2: Confirm it is served and valid**

Run: `python -m pytest tests/test_port_page.py::test_static_assets_served -v`
Expected: PASS (200).

- [ ] **Step 3: Commit**

```bash
git add src/mai/web/static/board.css
git -c user.name="r-log" commit -m "feat: port board stylesheet"
```

---

## Task 3: portboard.js (the live board app)

**Files:**
- Modify: `src/mai/web/static/portboard.js`

**Interfaces:** Consumes `GET /api/board` and `POST /api/board/{id}/{action}` (B2). No automated JS test — verified by Task 4's e2e smoke + review against this contract.

- [ ] **Step 1: Write the board app**

Replace `src/mai/web/static/portboard.js` with:

```javascript
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
        renderSummary(); renderFilters(); render(); });
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
    var dot = '<span class="tdot" style="background:' + (TIER[c.tier] || '#59636e') + '"></span>';
    var link = c.source_url
      ? '<a class="src-link" href="' + esc(c.source_url) + '" target="_blank" rel="noopener">↗</a>' : '';
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
```

- [ ] **Step 2: Lint-check by serving (no JS unit harness in this project)**

Run: `python -m pytest tests/test_port_page.py::test_static_assets_served -v`
Expected: PASS (file served). Behavioral verification happens in Task 4.

- [ ] **Step 3: Commit**

```bash
git add src/mai/web/static/portboard.js
git -c user.name="r-log" commit -m "feat: live port board UI (fetch, render, views, filters, mutations)"
```

---

## Task 4: End-to-end HTTP smoke + manual checklist

**Files:**
- Test: `tests/test_port_e2e.py`

**Interfaces:** drives the real app over HTTP (no browser) to prove the wiring: gate → page → API → mutation → overlay.

- [ ] **Step 1: Write the e2e test**

Create `tests/test_port_e2e.py`:

```python
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mai.auth.accounts import create_account
from mai.auth.fake import FakeHasher
from mai.board.service import apply_action
from mai.db.base import Base
from mai.web.app import create_app


@pytest_asyncio.fixture
async def env():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    hasher = FakeHasher()
    async with factory() as s:
        pw = await create_account(s, hasher, "dev", is_maintainer=False)
        await s.commit()
    app = create_app(factory, hasher, "test-secret", cookie_secure=False)
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test", follow_redirects=False) as ac:
        yield ac, factory, pw


async def test_full_claim_flow_over_http(env):
    ac, factory, pw = env
    # log in + clear forced password change
    await ac.post("/login", data={"username": "dev", "password": pw})
    await ac.post("/set-password", data={"new_password": "a-good-long-password"})

    # the gated page renders
    page = await ac.get("/port")
    assert page.status_code == 200 and 'id="port-board"' in page.text

    # the API gives us a csrf token
    board = (await ac.get("/api/board")).json()
    token = board["csrf"]
    assert board["me"] == {"username": "dev", "is_maintainer": False}

    # claim a card via the same path the JS uses
    r = await ac.post("/api/board/pgZ:three/claim", json={"csrf": token})
    assert r.status_code == 200
    assert r.json()["assignee"] == "dev"
    assert r.json()["status"] == "claimed"

    # the overlay is now visible to everyone via the API (_orphans here, since
    # pgZ:three is not a real engine candidate in this empty DB)
    after = (await ac.get("/api/board")).json()
    orphan = {o["port_candidate_id"]: o for o in after["_orphans"]}
    assert orphan["pgZ:three"]["assignee"] == "dev"


async def test_mutation_without_csrf_blocked_over_http(env):
    ac, _, pw = env
    await ac.post("/login", data={"username": "dev", "password": pw})
    await ac.post("/set-password", data={"new_password": "a-good-long-password"})
    r = await ac.post("/api/board/pgZ:three/claim", json={})
    assert r.status_code == 403
```

- [ ] **Step 2: Run the e2e test + full suite**

Run: `python -m pytest tests/test_port_e2e.py -v`
Expected: PASS (2 passed).
Run: `python -m pytest -q`
Expected: all green.

- [ ] **Step 3: Manual browser smoke (document the result in the commit/PR, do not block on it in CI)**

Run locally:
```bash
python -m mai.cli init-db
python -m mai.cli user-add me --maintainer
python -m mai.cli serve-web        # http://127.0.0.1:8000
```
Open `http://127.0.0.1:8000`, log in with the one-time password, set a new password, and confirm: the board renders four "Port into …" columns; **All cores / My ports / By person** toggles switch the layout; tier/source/subsystem/search filters narrow the cards; **claim** assigns you; **status** updates the pill; as a maintainer, **assign…** and **dismiss** work; the **↗** source link opens the commit. (With an empty DB the columns are empty — load real data first via the Phase A pipeline if you want populated columns.)

- [ ] **Step 4: Commit**

```bash
git add tests/test_port_e2e.py
git -c user.name="r-log" commit -m "test: end-to-end HTTP smoke for the port board"
```

---

## Self-Review

**Spec coverage (`port-debt-board-multiuser.md` §8 UX, Phase B B3 slice):**
- "login form is the only thing unauthenticated visitors reach; board behind the session" → `/port` gated (Task 1).
- "spine = Port into ZERO/ONE/TWO/THREE, tier-sorted, every fork shown" → render reuses `/api/board` columns (already sorted server-side) (Task 3).
- "view toggles: All cores · My ports · By person" → Task 3 `columnsForView`.
- "filters: tier · subsystem · source · text search" → Task 1 shell + Task 3 `applyFilters`.
- "card: tier dot · from · title · subsystem·magnitude · assignee chip or [Claim] · status pill · source link · evidence expand; maintainers see Assign/Dismiss" → Task 3 `cardHTML`.
- "freshness indicator" → Task 1 `#port-fresh` + Task 3 `renderSummary` ("as of now"; a precise timestamp can come from a future `/api/board` field).
- "fail-soft: a mutation conflict shows a notice, never silently drops" → Task 3 `mutate` 409/403/400 → toast.
- CSRF on mutations (§10) → Task 3 sends `csrf` from the payload.

**Deferred (not gaps):** per-event **history** view in the card (the `BoardEvent` audit exists server-side; the UI shows current state + evidence, not the full event log — a future enhancement); a precise "updated N min ago" timestamp (needs a `last_refresh` field on `/api/board`); the carried B2 Minors (inline-overlay test, CSRF-stability test) — `test_port_e2e` now exercises the inline/orphan overlay and a real claim, partially closing them.

**JS testing honesty:** `portboard.js` has no unit test (the project has no JS harness — identical to the v1 `portboard.js`). It is verified by the delivery test (served), the HTTP-level e2e (`test_port_e2e`, which drives the exact endpoints the JS calls), code review against the `/api/board` contract, and the manual browser checklist.

**Placeholder scan:** the Task 1 static files are intentional one-line placeholders that Tasks 2–3 fill — each is a real file with a tracked commit, and the route works against them immediately. No `TODO`/`TBD` remains after Task 3.

**Type/contract consistency:** the JS reads exactly the `/api/board` keys B2 produces (`summary`, `columns[].candidates[].{id,title,source_core,source_url,subsystem,tier,magnitude,evidence,board}`, `_orphans`, `csrf`, `me.{username,is_maintainer}`) and POSTs `{csrf, value?|reason?|related_pr?}` to `/api/board/{id}/{action}` — matching the Task-4 e2e and the B2 routes. `_overlay` shape returned by a successful POST (`{assignee,status,related_pr,dismissed,dismiss_reason}`) is assigned back to `candidate.board`, the same shape `GET /api/board` overlays.

---

## Execution Handoff

After saving, choose execution mode:
1. **Subagent-Driven (recommended)** — a fresh subagent per task, two-stage review between tasks.
2. **Inline Execution** — execute in this session with checkpoints.
