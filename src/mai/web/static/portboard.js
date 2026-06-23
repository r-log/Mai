// Port-Debt command center: per-core debt radar + a confident-ports worklist
// + a closeness-ranked review backlog. One row = one (fix -> target core) task
// = one claimable BoardItem. Read-only data from /api/board; mutations POST back.
let data = null, me = null, csrf = "";
const state = { core: "", sub: "", src: "", tier: "", q: "", mine: false, far: false };

const $ = (s, r = document) => r.querySelector(s);
const esc = (s) => (s == null ? "" : String(s)).replace(/[&<>"']/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const CORES = ["zero", "one", "two", "three", "four"];
const EXP = { zero: "Vanilla", one: "TBC", two: "WotLK", three: "Cata", four: "MoP" };
const TIER_RANK = { surgical: 0, small: 1, moderate: 2, bulk: 3 };
const cap = (c) => c.charAt(0).toUpperCase() + c.slice(1);
const ci = (c) => { const i = CORES.indexOf(c); return i < 0 ? 99 : i; };

async function load() {
  const r = await fetch("/api/board");
  const j = await r.json();
  data = j; me = j.me; csrf = j.csrf;
  buildFilters();
  render();
}

// Flatten fixes into per-(fix,core) tasks, split by lane.
function tasks() {
  const ready = [], review = [], far = [];
  for (const f of data.fixes) {
    for (const n of f.needs)
      ready.push({ f, core: n.core, id: n.item_id, board: n.board, kind: "needs" });
    for (const r of f.review) {
      const t = { f, core: r.core, id: r.item_id, board: r.board, kind: "review",
                  reason: r.reason, applied: r.applied, total: r.total, band: r.band };
      (r.band === "near" || r.band === "partial" ? review : far).push(t);
    }
  }
  return { ready, review, far };
}

function pass(t) {
  if (state.core && t.core !== state.core) return false;
  if (state.sub && t.f.subsystem !== state.sub) return false;
  if (state.src && t.f.source_core !== state.src) return false;
  if (state.tier && t.f.tier !== state.tier) return false;
  if (state.q && !(t.f.title + " " + t.f.subsystem).toLowerCase().includes(state.q))
    return false;
  if (state.mine && !(t.board && t.board.assignee === me.username)) return false;
  return true;
}

function buildFilters() {
  const subs = [...new Set(data.fixes.map(f => f.subsystem))].sort();
  for (const s of subs) {
    const o = document.createElement("option"); o.value = s; o.textContent = s;
    $("#f-subsystem").appendChild(o);
  }
  const srcs = [...new Set(data.fixes.map(f => f.source_core))].sort(
    (a, b) => ci(a) - ci(b));
  for (const s of srcs) {
    const o = document.createElement("option");
    o.value = s; o.textContent = `from ${cap(s)}`;
    $("#f-source").appendChild(o);
  }
}

// ---- render ------------------------------------------------------------
function render() {
  const all = tasks();
  $("#cc-summary").textContent =
    `${all.ready.length} ready · ${all.review.length} to review · 5 forks`;
  renderRadar(all);
  renderReady(all.ready.filter(pass));
  renderReview(all.review.filter(pass));
  renderFar(all.far);
  $("#f-clear").hidden = !state.core;
  $("#f-mine").dataset.on = state.mine ? "1" : "0";
}

function renderRadar(all) {
  const need = {}, rev = {};
  for (const c of CORES) { need[c] = 0; rev[c] = 0; }
  for (const t of all.ready) need[t.core]++;
  for (const t of all.review) rev[t.core]++;
  const maxRev = Math.max(1, ...CORES.map(c => rev[c]));
  $("#cc-radar").innerHTML = CORES.map(c => {
    const w = Math.round(100 * rev[c] / maxRev);
    return `<button class="radar-tile${state.core === c ? " on" : ""}" data-core="${c}">
      <span class="rt-core">${cap(c)}<span class="rt-exp">${EXP[c]}</span></span>
      <span class="rt-need">${need[c]}<span class="rt-lab">ready</span></span>
      <span class="rt-bar"><span style="width:${w}%"></span></span>
      <span class="rt-rev">${rev[c]} review</span>
    </button>`;
  }).join("");
}

function assigneeBtn(t) {
  const ov = t.board || {};
  if (!ov.assignee)
    return `<button class="claim" data-act="claim" data-id="${t.id}">claim</button>`;
  const mine = ov.assignee === me.username;
  const cls = mine ? "claim mine" : "claim taken";
  const act = mine ? "unassign" : (me.is_maintainer ? "assign" : "");
  return `<button class="${cls}" ${act ? `data-act="${act}"` : "disabled"}
    data-id="${t.id}" title="${esc(ov.status || "")}">${mine ? "✓ you" : "@" + esc(ov.assignee)}</button>`;
}

function metaCols(t) {
  return `<a class="t-core core-${t.core}" title="port into ${cap(t.core)} (${EXP[t.core]})">→${cap(t.core)}</a>
    <span class="t-sub">${esc(t.f.subsystem)}</span>
    <span class="t-title">${esc(t.f.title)}</span>
    <span class="t-from">from ${cap(t.f.source_core)}</span>`;
}

function readyRow(t) {
  const src = t.f.source_url
    ? `<a class="t-link" href="${esc(t.f.source_url)}" target="_blank" rel="noopener">↗</a>` : "";
  return `<div class="task t-ready" data-id="${t.id}">
    ${assigneeBtn(t)}
    ${metaCols(t)}
    <span class="t-tier tier-${t.f.tier}">${t.f.tier}</span>
    ${src}
    <button class="t-why" data-why title="why is this safe to port?">i</button>
    <div class="t-proof" hidden>
      <b>Clean apply + all-shared.</b> Every file this fix touches is cross-fork
      infrastructure (<code>${esc(t.f.subsystem)}</code>), and the patch applies
      byte-clean to ${cap(t.core)} — the surrounding code there is identical, so it is
      the same bug and the same fix. ${t.f.magnitude} lines.
    </div></div>`;
}

function reviewRow(t) {
  const frac = t.total ? t.applied / t.total : 0;
  const pct = Math.round(frac * 100);
  const src = t.f.source_url
    ? `<a class="t-link" href="${esc(t.f.source_url)}" target="_blank" rel="noopener">↗</a>` : "";
  return `<div class="task t-review band-${t.band}" data-id="${t.id}">
    ${assigneeBtn(t)}
    ${metaCols(t)}
    <span class="meter" title="${esc(t.reason)}"><span style="width:${pct}%"></span></span>
    <span class="t-frac">${t.applied}/${t.total}</span>
    ${src}
    <button class="t-why" data-why title="${esc(t.reason)}">i</button>
    <div class="t-proof" hidden><b>Review — diverged.</b> ${esc(t.reason)}.
      ${pct}% of this fix applies to ${cap(t.core)}; the rest conflicts because the
      code there differs (expansion or client divergence). A human should adapt it.
      ${t.f.magnitude} lines.</div></div>`;
}

function renderReady(rows) {
  $("#ready-ct").textContent = rows.length;
  const list = $("#ready-list");
  if (!rows.length) {
    list.innerHTML = `<div class="cc-empty">no confident ports match — try clearing filters</div>`;
    return;
  }
  // group by target core (skip grouping when a single core is filtered)
  const groups = state.core ? [state.core] : CORES;
  list.innerHTML = groups.map(c => {
    const g = rows.filter(t => t.core === c).sort((a, b) =>
      (TIER_RANK[a.f.tier] - TIER_RANK[b.f.tier]) || a.f.magnitude - b.f.magnitude);
    if (!g.length) return "";
    const head = state.core ? "" :
      `<div class="grp">Into ${cap(c)} <span class="grp-exp">${EXP[c]}</span><span class="grp-ct">${g.length}</span></div>`;
    return head + g.map(readyRow).join("");
  }).join("");
}

function renderReview(rows) {
  $("#review-ct").textContent = rows.length;
  rows.sort((a, b) => {
    const ba = a.band === "near" ? 0 : 1, bb = b.band === "near" ? 0 : 1;
    return ba - bb || (b.applied / b.total) - (a.applied / a.total);
  });
  const list = $("#review-list");
  list.innerHTML = rows.length
    ? rows.slice(0, 400).map(reviewRow).join("") +
      (rows.length > 400 ? `<div class="cc-more">showing 400 of ${rows.length} — narrow with filters</div>` : "")
    : `<div class="cc-empty">nothing in review matches</div>`;
}

function renderFar(far) {
  const shown = far.filter(pass);
  const btn = $("#far-toggle"), list = $("#far-list");
  btn.textContent = state.far
    ? `▾ hide ${shown.length} diverged`
    : `▸ ${shown.length} diverged (far from applying — usually by design)`;
  list.hidden = !state.far;
  if (state.far)
    list.innerHTML = shown.slice(0, 300).map(reviewRow).join("") +
      (shown.length > 300 ? `<div class="cc-more">showing 300 of ${shown.length}</div>` : "");
}

// ---- interaction -------------------------------------------------------
async function mutate(id, action, payload) {
  const r = await fetch(`/api/board/${encodeURIComponent(id)}/${action}`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ csrf, ...payload }),
  });
  if (r.status === 200) await load();
  else if (r.status === 409) { alert("already claimed by someone else"); await load(); }
  else if (r.status === 403) alert("maintainers only");
  else alert("error: " + (await r.text()));
}

document.addEventListener("click", (e) => {
  const tile = e.target.closest(".radar-tile");
  if (tile) { state.core = state.core === tile.dataset.core ? "" : tile.dataset.core; render(); return; }
  if (e.target.closest("#f-clear")) { state.core = ""; render(); return; }
  if (e.target.closest("#f-mine")) { state.mine = !state.mine; render(); return; }
  if (e.target.closest("#far-toggle")) { state.far = !state.far; renderFar(tasks().far); return; }
  const why = e.target.closest("[data-why]");
  if (why) { const p = why.parentElement.querySelector(".t-proof"); p.hidden = !p.hidden; return; }
  const act = e.target.closest("[data-act]");
  if (act) {
    const id = act.dataset.id, a = act.dataset.act;
    if (a === "assign") { const u = prompt("assign to username:"); if (u) mutate(id, "assign", { value: u }); }
    else mutate(id, a, {});
  }
});
["f-subsystem", "f-source", "f-tier"].forEach(id =>
  $("#" + id).addEventListener("change", (e) => {
    state[id === "f-subsystem" ? "sub" : id === "f-source" ? "src" : "tier"] = e.target.value;
    render();
  }));
$("#f-search").addEventListener("input", (e) => {
  state.q = e.target.value.trim().toLowerCase(); render();
});

load();
