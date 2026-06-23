let data = null, me = null, csrf = "";
const $ = (s, r = document) => r.querySelector(s);
const esc = (s) => (s || "").replace(/[&<>"]/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const TIER = { surgical: "#1a7f37", small: "#9a6700", moderate: "#bc4c00", bulk: "#cf222e" };
const BAND = { near: "#1a7f37", partial: "#9a6700", far: "#8b949e" };

async function load() {
  const r = await fetch("/api/board");
  const j = await r.json();
  data = j; me = j.me; csrf = j.csrf;
  $("#port-summary").textContent =
    `${j.summary.fixes} fixes · ${j.summary.needs} needs · ${j.summary.review} review`;
  populateFilters();
  render();
}

function populateFilters() {
  const fc = $("#f-core");
  for (const c of data.cores) {
    const o = document.createElement("option");
    o.value = c; o.textContent = `needs porting to ${c}`;
    fc.appendChild(o);
  }
  const subs = [...new Set(data.fixes.map(f => f.subsystem))].sort();
  for (const s of subs) {
    const o = document.createElement("option"); o.value = s; o.textContent = s;
    $("#f-subsystem").appendChild(o);
  }
  const srcs = [...new Set(data.fixes.map(f => f.source_core))].sort();
  for (const s of srcs) {
    const o = document.createElement("option"); o.value = s; o.textContent = `from ${s}`;
    $("#f-source").appendChild(o);
  }
}

function chip(entry, kind) {
  // kind: "needs" | "review" | "na" | "has_it"
  const ov = entry.board || {};
  const mine = ov.assignee && ov.assignee === me.username;
  const claimable = kind === "needs" || kind === "review";
  const cls = ["mchip", `mchip-${kind}`];
  if (entry.band) cls.push(`band-${entry.band}`);
  if (ov.assignee) cls.push("claimed");
  const label = entry.core;
  const title = kind === "review" ? esc(entry.reason)
    : kind === "na" ? esc(entry.reason) : "";
  const who = ov.assignee ? `<span class="mchip-who">${esc(ov.assignee)}</span>` : "";
  const act = claimable
    ? `<button class="mchip-act" data-act="${ov.assignee ? (mine ? "unassign" : "assign") : "claim"}"
        data-id="${entry.item_id}">${ov.assignee ? (mine ? "✓" : "@") : "+"}</button>`
    : "";
  return `<span class="${cls.join(" ")}" title="${title}">${esc(label)}${who}${act}</span>`;
}

function row(label, entries, kind) {
  if (!entries.length) return "";
  const far = kind === "review" ? entries.filter(e => e.band === "far") : [];
  const near = kind === "review" ? entries.filter(e => e.band !== "far") : entries;
  const chips = near.map(e => chip(e, kind)).join("");
  const farChips = far.length
    ? `<span class="mrow-far" data-far hidden>${far.map(e => chip(e, kind)).join("")}</span>
       <button class="mrow-more" data-more>+${far.length} diverged</button>`
    : "";
  return `<div class="mrow mrow-${kind}"><span class="mrow-lab">${label}</span>
            <span class="mrow-chips">${chips}${farChips}</span></div>`;
}

function cardHTML(f) {
  const src = f.source_url
    ? `<a class="src-link" href="${esc(f.source_url)}" target="_blank">↗</a>` : "";
  const cores = new Set([...f.needs, ...f.review].map(e => e.core));
  const dataCore = [...cores].join(",");
  return `<article class="fcard" data-id="${esc(f.id)}" data-tier="${f.tier}"
      data-source="${f.source_core}" data-subsystem="${esc(f.subsystem)}"
      data-cores="${dataCore}" data-text="${esc((f.title + " " + f.subsystem).toLowerCase())}">
    <div class="fc-top"><span class="tdot" style="background:${TIER[f.tier] || "#888"}"></span>
      <span class="fc-from">from ${esc(f.source_core)}</span>${src}</div>
    <div class="fc-title">${esc(f.title)}</div>
    <div class="fc-meta">${esc(f.subsystem)} · ${f.magnitude} lines · ${f.tier}</div>
    ${row("NEEDS", f.needs, "needs")}
    ${row("REVIEW", f.review, "review")}
    ${row("HAS IT", f.has_it, "has_it")}
    ${row("N/A", f.na, "na")}
  </article>`;
}

function render() {
  const board = $("#port-board");
  board.classList.add("fix-grid");
  board.innerHTML = data.fixes.length
    ? data.fixes.map(cardHTML).join("")
    : `<div class="empty-state">nothing to port — every fix is present or divergent</div>`;
  applyFilters();
}

function applyFilters() {
  const core = $("#f-core").value, tier = $("#f-tier").value,
    src = $("#f-source").value, sub = $("#f-subsystem").value,
    q = $("#f-search").value.trim().toLowerCase(),
    view = $("#port-views .on")?.dataset.view || "all";
  for (const card of document.querySelectorAll(".fcard")) {
    const cs = (card.dataset.cores || "").split(",");
    let show = true;
    if (core && !cs.includes(core)) show = false;
    if (tier && card.dataset.tier !== tier) show = false;
    if (src && card.dataset.source !== src) show = false;
    if (sub && card.dataset.subsystem !== sub) show = false;
    if (q && !card.dataset.text.includes(q)) show = false;
    if (view === "mine") {
      const mineHere = card.querySelector(".mchip.claimed .mchip-who");
      show = show && !![...card.querySelectorAll(".mchip-who")]
        .find(w => w.textContent === me.username);
    }
    card.hidden = !show;
  }
}

async function mutate(id, action, payload) {
  const r = await fetch(`/api/board/${encodeURIComponent(id)}/${action}`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ csrf, ...payload }),
  });
  if (r.status === 200) { await load(); }
  else if (r.status === 409) { alert("already claimed by someone else"); await load(); }
  else if (r.status === 403) { alert("not allowed"); }
  else { alert("error: " + (await r.text())); }
}

document.addEventListener("click", (e) => {
  const more = e.target.closest("[data-more]");
  if (more) { const f = more.previousElementSibling; f.hidden = !f.hidden;
    more.textContent = f.hidden ? more.textContent.replace("hide", "+") : "hide diverged";
    return; }
  const act = e.target.closest(".mchip-act");
  if (act) {
    const id = act.dataset.id, a = act.dataset.act;
    if (a === "assign") { const u = prompt("assign to username:"); if (u) mutate(id, "assign", { value: u }); }
    else mutate(id, a, {});
    return;
  }
});
["f-core", "f-tier", "f-source", "f-subsystem"].forEach(id =>
  $("#" + id).addEventListener("change", applyFilters));
$("#f-search").addEventListener("input", applyFilters);
document.querySelectorAll("#port-views button").forEach(b =>
  b.addEventListener("click", () => {
    document.querySelectorAll("#port-views button").forEach(x => x.classList.remove("on"));
    b.classList.add("on"); applyFilters();
  }));

load();
