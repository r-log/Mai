// Personal Control Center: My todo / Shipped / Available, plus activity,
// team and project panels. One human action only — "Drop" (unassign). The
// engine auto-resolves a task when the port lands; nothing is closed by hand.
let d = null, csrf = "";
const $ = (s) => document.querySelector(s);
const esc = (s) => (s == null ? "" : String(s)).replace(/[&<>"']/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const cap = (c) => (c ? c.charAt(0).toUpperCase() + c.slice(1) : "");
const EXP = { zero: "Vanilla", one: "TBC", two: "WotLK", three: "Cata", four: "MoP" };

function ago(iso) {
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 90) return "just now";
  if (s < 3600) return Math.round(s / 60) + "m ago";
  if (s < 86400) return Math.round(s / 3600) + "h ago";
  return Math.round(s / 86400) + "d ago";
}

async function load() {
  const r = await fetch("/api/me");
  d = await r.json(); csrf = d.csrf;
  cards(); todo(); shipped(); spark(); activity(); team(); project();
}

function cards() {
  const s = d.stats;
  $("#me-cards").innerHTML = `
    <div class="mecard mc-todo"><div class="mc-n">${s.todo}</div>
      <div class="mc-l">My todo</div>
      <div class="mc-sub">${s.self} grabbed · ${s.assigned} assigned</div></div>
    <div class="mecard mc-ship"><div class="mc-n">${s.shipped}</div>
      <div class="mc-l">Shipped</div><div class="mc-sub">auto-resolved</div></div>
    <div class="mecard mc-grab"><div class="mc-n">${s.available}</div>
      <div class="mc-l">Available to grab</div>
      <a class="mc-sub mc-link" href="/port">go to board →</a></div>`;
}

function taskRow(t, opts = {}) {
  const core = t.core
    ? `<span class="t-core core-${t.core}" title="${EXP[t.core] || ""}">→${cap(t.core)}</span>` : "";
  const src = t.source_url
    ? `<a class="t-link" href="${esc(t.source_url)}" target="_blank" rel="noopener">↗</a>` : "";
  const right = opts.shipped
    ? `<span class="t-when shipped">✓ ${ago(t.at)}</span>`
    : `<span class="t-via ${t.via}">${t.via === "assigned" ? "assigned" : "grabbed"}</span>
       <span class="t-when">${ago(t.since)}</span>
       <button class="drop" data-id="${esc(t.item_id)}">Drop</button>`;
  return `<div class="task">${core}
    <span class="t-sub">${esc(t.subsystem)}</span>
    <span class="t-title">${esc(t.title)}</span>${src}${right}</div>`;
}

function todo() {
  $("#todo-ct").textContent = d.queue.length;
  $("#todo-list").innerHTML = d.queue.length
    ? d.queue.map(t => taskRow(t)).join("")
    : `<div class="cc-empty">nothing taken yet — grab one from the <a href="/port">board</a></div>`;
}

function shipped() {
  $("#ship-ct").textContent = d.shipped.length;
  $("#ship-list").innerHTML = d.shipped.length
    ? d.shipped.map(t => taskRow(t, { shipped: true })).join("")
    : `<div class="cc-empty">nothing shipped yet</div>`;
}

function spark() {
  const max = Math.max(1, ...d.spark.map(x => x.n));
  $("#me-spark").innerHTML = d.spark.map(x => {
    const h = Math.round(4 + 30 * x.n / max);
    return `<span class="sp-bar" title="${x.d}: ${x.n}"><span style="height:${h}px"></span></span>`;
  }).join("") + `<div class="sp-cap">events / day · last 14</div>`;
}

const VERB = { claim: "took", assign: "was assigned", unassign: "dropped",
               status: "updated", link_pr: "linked a PR on", dismiss: "dismissed",
               restore: "restored" };
function activity() {
  $("#me-activity").innerHTML = d.activity.length
    ? d.activity.map(e => `<div class="act">
        <span class="act-v">${VERB[e.action] || e.action}</span>
        <span class="act-t">${esc(e.title)}</span>
        <span class="act-w">${ago(e.at)}</span></div>`).join("")
    : `<div class="cc-empty">no activity yet</div>`;
}

function team() {
  if (!d.team.length) { $("#me-team").innerHTML = `<div class="cc-empty">no claims yet</div>`; return; }
  const max = Math.max(1, ...d.team.map(u => u.todo + u.shipped));
  $("#me-team").innerHTML = d.team.map(u => {
    const wt = Math.round(100 * u.todo / max), ws = Math.round(100 * u.shipped / max);
    const meCls = u.user === d.me ? " me" : "";
    return `<div class="tm${meCls}"><span class="tm-u">${esc(u.user)}</span>
      <span class="tm-bar"><span class="tm-todo" style="width:${wt}%"></span><span class="tm-ship" style="width:${ws}%"></span></span>
      <span class="tm-n">${u.todo}·${u.shipped}</span></div>`;
  }).join("") + `<div class="tm-key"><span class="k-todo"></span>todo <span class="k-ship"></span>shipped</div>`;
}

function project() {
  const p = d.project, pct = p.needs_total ? Math.round(100 * p.needs_claimed / p.needs_total) : 0;
  $("#me-project").innerHTML = `
    <div class="pj-bar"><span style="width:${pct}%"></span></div>
    <div class="pj-row"><b>${p.needs_claimed}</b> of <b>${p.needs_total}</b> confident ports taken
      · <a href="/port">${p.unclaimed} free</a></div>`;
}

async function drop(id) {
  await fetch(`/api/board/${encodeURIComponent(id)}/unassign`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ csrf }),
  });
  await load();
}

document.addEventListener("click", (e) => {
  const b = e.target.closest(".drop");
  if (b) drop(b.dataset.id);
});

load();
