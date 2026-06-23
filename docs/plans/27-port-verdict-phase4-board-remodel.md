# Port-Verdict Phase 4 — Board Re-model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Re-point the port-debt board off the old `PortCandidate` (one row per absent target) onto the truthful `PortVerdict` table, presented as **one card per fix** with a cross-core NEEDS/REVIEW/N-A/HAS-IT matrix, REVIEW ranked by conflict closeness.

**Architecture:** A new export `build_port_verdicts(session)` groups `PortVerdict` rows by `patch_group_id` into per-fix cards (each listing which cores need / should-review / already-have / can't-use the fix). `/api/board` and `reconcile_board` read that export instead of `build_port_candidates`; `run_refresh_cycle` runs `compute_verdicts` so the data is fresh. The `/port/` page re-renders as a grid of fix-cards with a "needs porting to [core]" filter. The board state machinery (`BoardItem` keyed `{patch_group_id}:{target_core}`, `apply_action`, `BoardEvent`, claim/assign/dismiss) is **unchanged** — only the export shape, the API/reconcile source, and the UI change.

**Tech Stack:** Python 3.12, async SQLAlchemy 2.0, Starlette, pytest (asyncio_mode=auto), vanilla JS (no framework), uvicorn for the run-skill smoke.

## Global Constraints

- **Truthfulness gate is untouched.** `build_port_verdicts` only *reads* `PortVerdict.verdict`; it never re-derives a verdict. NEEDS shown to the user are exactly the engine's `needs` rows (clean apply + all-shared). No new logic may promote a `review` to `needs`.
- **BoardItem key is `f"{patch_group_id}:{core}"`** — the same composite the old export produced. Every claimable matrix entry MUST carry `item_id` in that exact form so existing `apply_action`/overlays/events keep working with zero migration.
- **No AI attribution** in commits (no `Co-Authored-By: Claude`, no "Generated with" footer). Conventional-commit style (`feat:`/`refactor:`/`test:`), matching the repo log.
- **4-space indent, ~88 col** Python; match the existing `dataviz.py` / `board_api.py` style.
- `_CORE_ORDER` must include all five forks: `{"zero":0,"one":1,"two":2,"three":3,"four":4}`.
- A fix-card is emitted **only if it has ≥1 `needs` or `review` entry** (a fix that is `has_it`/`n/a` everywhere is not actionable → no card). `na`/`has_it` are shown *inside* actionable cards as proof-of-check.
- Closeness band thresholds come from `mai.sync.verdicts.closeness_label` (near ≥0.8 / partial ≥0.4 / far) — do not re-define them.

---

## File Structure

| File | Change | Responsibility |
|------|--------|----------------|
| `src/mai/publish/dataviz.py` | Modify | Add `build_port_verdicts` + `_review_reason`/`_band_rank` helpers; add `four` to `_CORE_ORDER`. Leave `build_port_candidates` in place (now board-unused; removed in a later cleanup). |
| `src/mai/web/board_api.py` | Modify | `/api/board` calls `build_port_verdicts`; overlay-merge per matrix `item_id`; orphan list from unmatched BoardItems. POST action route unchanged. |
| `src/mai/refresh/cycle.py` | Modify | `reconcile_board` reads the verdict export's actionable `item_id` set; `run_refresh_cycle` calls `compute_verdicts(session, git_client)` after classify, before reconcile. |
| `src/mai/web/static/portboard.js` | Rewrite | Render one card per fix + the four-row matrix; "needs porting to [core]" filter; FAR-collapse; per-entry claim/assign. |
| `src/mai/web/app.py` | Modify | `_port_html` shell: add the `#f-core` select; keep existing filters. |
| `src/mai/web/static/board.css` | Modify | Card-grid (replace 4-column layout); matrix rows + per-verdict core chips. |
| `tests/test_build_port_verdicts.py` | Create | Export grouping, matrix split, card-suppression, closeness sort, summary. |
| `tests/test_board_api_verdicts.py` | Create | `/api/board` new shape + overlay merge by `item_id`. |
| `tests/test_reconcile_verdicts.py` | Create | Archive-when-not-actionable against `PortVerdict`. |

---

### Task 1: `build_port_verdicts` export

**Files:**
- Modify: `src/mai/publish/dataviz.py`
- Test: `tests/test_build_port_verdicts.py`

**Interfaces:**
- Consumes: `PortVerdict` (model), `Commit`, `Repo`, `_source_repos`, `_TIER_RANK`, `closeness_label` (from `mai.sync.verdicts`).
- Produces: `async build_port_verdicts(session) -> dict` with shape:
  ```json
  {
    "summary": {"needs": int, "review": int, "na": int, "has_it": int, "fixes": int},
    "cores": ["zero","one","two","three","four"],
    "fixes": [
      {"id": "<pg_id>", "title": str, "source_core": str, "source_url": str|null,
       "subsystem": str, "tier": str, "magnitude": int,
       "needs":   [{"core": str, "item_id": "<pg_id>:<core>"}],
       "review":  [{"core": str, "item_id": "<pg_id>:<core>", "reason": str,
                    "applied": int?, "total": int?, "band": "near|partial|far"?}],
       "na":      [{"core": str, "reason": str}],
       "has_it":  [{"core": str}]}
    ]
  }
  ```

- [ ] **Step 1: Write the failing test**

```python
# tests/test_build_port_verdicts.py
from mai.db.session import SessionFactory, engine
from mai.db.models import Base, PatchGroup, Commit, Repo, PortVerdict
from mai.publish.dataviz import build_port_verdicts


async def _seed():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with SessionFactory() as s:
        s.add(Repo(full_name="mangosthree/server", core="three",
                   url="https://github.com/mangosthree/server"))
        s.add(PatchGroup(id="pg1", patch_id="pid1"))
        s.add(Commit(core="three", sha="abc1234567", author="a", authored_at=None,
                     committer="a", committed_at=None, message="Fix shared thing"))
        # three has it (source), two needs, one review-conflict, four n/a
        s.add(PortVerdict(patch_group_id="pg1", core="two", verdict="needs",
                          apply_result="clean", relevance="portable",
                          source_core="three", source_sha="abc1234567",
                          subsystem="src/shared/Database", magnitude=10, tier="surgical"))
        s.add(PortVerdict(patch_group_id="pg1", core="one", verdict="review",
                          apply_result="conflict", relevance="divergent",
                          source_core="three", source_sha="abc1234567",
                          subsystem="src/shared/Database", magnitude=10, tier="surgical",
                          conflict_applied=4, conflict_total=5))
        s.add(PortVerdict(patch_group_id="pg1", core="four", verdict="not_applicable",
                          apply_result="file_absent", relevance="divergent",
                          source_core="three", source_sha="abc1234567",
                          subsystem="src/shared/Database", magnitude=10, tier="surgical"))
        await s.commit()


async def test_export_groups_one_card_per_fix(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path/'t.db'}")
    # NOTE: SessionFactory binds at import; this test uses the default in-memory/file db.
    await _seed()
    async with SessionFactory() as s:
        out = await build_port_verdicts(s)
    assert out["summary"] == {"needs": 1, "review": 1, "na": 1, "has_it": 0, "fixes": 1}
    assert out["cores"] == ["zero", "one", "two", "three", "four"]
    card = out["fixes"][0]
    assert card["id"] == "pg1"
    assert card["title"] == "Fix shared thing"
    assert card["needs"] == [{"core": "two", "item_id": "pg1:two"}]
    assert card["review"][0]["item_id"] == "pg1:one"
    assert card["review"][0]["band"] == "near"      # 4/5 = 0.8
    assert card["review"][0]["applied"] == 4 and card["review"][0]["total"] == 5
    assert card["na"] == [{"core": "four", "reason": "code not present"}]
```

> The test relies on the project's configured DB. If the suite already has a DB fixture/conftest, use it instead of `monkeypatch` — match the pattern in `tests/test_compute_verdicts.py`. Read that file first and mirror its session/DB setup verbatim.

- [ ] **Step 2: Run it, expect failure**

Run: `python -m pytest tests/test_build_port_verdicts.py -q`
Expected: FAIL — `build_port_verdicts` does not exist (ImportError).

- [ ] **Step 3: Implement the export**

In `src/mai/publish/dataviz.py`: add the import and helpers, update `_CORE_ORDER`, append the function.

```python
# add to the imports at top (PortVerdict to the models import; closeness_label new)
from mai.db.models import (Commit, DriftObservation, PatchGroup, PortCandidate,
                           PortVerdict, Report, Repo, SourceRecord, Verification)
from mai.sync.verdicts import closeness_label

# change the existing constant:
_CORE_ORDER = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4}


def _review_reason(v: "PortVerdict") -> str:
    if v.apply_result == "conflict" and v.conflict_total:
        b = closeness_label(v.conflict_applied, v.conflict_total)
        return f"conflict — {v.conflict_applied}/{v.conflict_total} hunks ({b})"
    if v.apply_result == "conflict":
        return "conflict — binary/blob change"
    return "diverged — needs adaptation"


def _band_rank(entry: dict) -> int:
    return {"near": 0, "partial": 1, "far": 2}.get(entry.get("band"), 3)


async def build_port_verdicts(session: AsyncSession) -> dict:
    """Per-fix cross-core port matrix for /port/, read straight off PortVerdict.

    One card per fix that has >=1 needs|review core; each card lists which cores
    need it (claimable), should be reviewed (claimable, with closeness), already
    have it, or can't use it. REVIEW is ranked near->partial->far. Truthful by
    construction: it never re-grades a verdict, only groups them.
    """
    repos = await _source_repos(session)
    rows = list(await session.scalars(select(PortVerdict)))
    by_fix: dict[str, list] = {}
    for v in rows:
        by_fix.setdefault(v.patch_group_id, []).append(v)
    cores = sorted({v.core for v in rows} | set(_CORE_ORDER),
                   key=lambda c: (_CORE_ORDER.get(c, 99), c))

    summary = {"needs": 0, "review": 0, "na": 0, "has_it": 0}
    fixes: list[dict] = []
    for pg_id, vs in by_fix.items():
        rep = vs[0]   # source_core/sha/subsystem/tier/magnitude identical across the group
        commit = await session.scalar(
            select(Commit).where(Commit.core == rep.source_core,
                                 Commit.sha == rep.source_sha))
        title = commit.message.strip().splitlines()[0] if commit and commit.message else ""
        if not title:
            title = f"{rep.subsystem} fix ({(rep.source_sha or '')[:8]})"
        repo = repos.get(rep.source_core)
        source_url = (f"https://github.com/{repo}/commit/{rep.source_sha}"
                      if repo and rep.source_sha else None)

        needs, review, na, has_it = [], [], [], []
        for v in sorted(vs, key=lambda v: (_CORE_ORDER.get(v.core, 99), v.core)):
            item_id = f"{pg_id}:{v.core}"
            if v.verdict == "needs":
                needs.append({"core": v.core, "item_id": item_id})
            elif v.verdict == "review":
                entry = {"core": v.core, "item_id": item_id, "reason": _review_reason(v)}
                if v.conflict_total:
                    entry["applied"] = v.conflict_applied
                    entry["total"] = v.conflict_total
                    entry["band"] = closeness_label(v.conflict_applied, v.conflict_total)
                review.append(entry)
            elif v.verdict == "has_it":
                has_it.append({"core": v.core})
            else:
                na.append({"core": v.core, "reason": "code not present"})
        if not needs and not review:
            continue   # not actionable -> no card

        review.sort(key=lambda e: (_band_rank(e),
                                   -(e.get("applied", 0) / e.get("total", 1))))
        summary["needs"] += len(needs)
        summary["review"] += len(review)
        summary["na"] += len(na)
        summary["has_it"] += len(has_it)
        fixes.append({
            "id": pg_id, "title": title, "source_core": rep.source_core,
            "source_url": source_url, "subsystem": rep.subsystem,
            "tier": rep.tier, "magnitude": rep.magnitude,
            "needs": needs, "review": review, "na": na, "has_it": has_it})

    def _best_band(f: dict) -> int:
        return min((_band_rank(e) for e in f["review"]), default=3)
    fixes.sort(key=lambda f: (0 if f["needs"] else 1, _best_band(f),
                              _TIER_RANK.get(f["tier"], 9), f["magnitude"]))
    summary["fixes"] = len(fixes)
    return {"summary": summary, "cores": cores, "fixes": fixes}
```

- [ ] **Step 4: Run the test, expect pass**

Run: `python -m pytest tests/test_build_port_verdicts.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mai/publish/dataviz.py tests/test_build_port_verdicts.py
git commit -m "feat: build_port_verdicts — per-fix cross-core matrix export"
```

---

### Task 2: `/api/board` reads the verdict export

**Files:**
- Modify: `src/mai/web/board_api.py`
- Test: `tests/test_board_api_verdicts.py`

**Interfaces:**
- Consumes: `build_port_verdicts`, `BoardItemRepository.active`, the existing `_overlay(item)` helper, the existing csrf/`me` plumbing.
- Produces: `GET /api/board` JSON = `build_port_verdicts` output **plus** per-entry `board` overlays, `_orphans`, `csrf`, `me`. The POST action route is unchanged.

Read `src/mai/web/board_api.py` first to reuse `_overlay`, the csrf token source, and the `me` block verbatim. Replace only the board-assembly portion of the GET handler.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_board_api_verdicts.py — uses the app's ASGI transport like test_*_api.py
# (read the existing board-api test to copy the httpx ASGITransport + login fixture).
# Assert GET /api/board returns {"summary","cores","fixes",...}, that a fix's needs
# entry gains a "board" overlay after a claim, and that "columns" is GONE.
```

> Mirror the transport/login setup from the current board-api test file exactly (find it: `grep -rl "/api/board" tests`). Assert: `body["fixes"]` exists, `"columns" not in body`, `body["summary"]["needs"]` is an int, and after POSTing `claim` to `pg:core`, the matching `needs`/`review` entry has `entry["board"]["assignee"] == <user>`.

- [ ] **Step 2: Run it, expect failure** — Run: `python -m pytest tests/test_board_api_verdicts.py -q` → FAIL (still returns `columns`).

- [ ] **Step 3: Implement** — in the GET `/api/board` handler:

```python
board = await build_port_verdicts(session)
items = await BoardItemRepository(session).active()
overlays = {it.port_candidate_id: _overlay(it) for it in items}
seen: set[str] = set()
for fix in board["fixes"]:
    for entry in (*fix["needs"], *fix["review"]):
        ov = overlays.get(entry["item_id"])
        if ov is not None:
            entry["board"] = ov
            seen.add(entry["item_id"])
board["_orphans"] = [ov for pcid, ov in overlays.items() if pcid not in seen]
board["csrf"] = <existing csrf expression>
board["me"] = {"username": username, "is_maintainer": <existing>}
return JSONResponse(board)
```

Update the import from `mai.publish.dataviz` to `build_port_verdicts` (drop `build_port_candidates` from this file's imports).

- [ ] **Step 4: Run the test, expect pass** — `python -m pytest tests/test_board_api_verdicts.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mai/web/board_api.py tests/test_board_api_verdicts.py
git commit -m "feat: /api/board serves the per-fix verdict matrix + overlays"
```

---

### Task 3: reconcile + refresh-cycle wiring

**Files:**
- Modify: `src/mai/refresh/cycle.py`
- Test: `tests/test_reconcile_verdicts.py`

**Interfaces:**
- Consumes: `build_port_verdicts`, `compute_verdicts` (from `mai.sync.verdicts`), `BoardItemRepository.active`, the `git_client` already passed to `run_refresh_cycle`.
- Produces: `reconcile_board(session) -> int` archiving any active `BoardItem` whose `port_candidate_id` is not in the actionable `item_id` set; `run_refresh_cycle` computes verdicts before reconciling.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_reconcile_verdicts.py
# Seed: one BoardItem "pg1:two" (active) where pg1->two is verdict=needs  -> kept.
#       one BoardItem "pgX:zero" (active) with NO actionable verdict       -> archived.
# Call reconcile_board(session); assert it returns 1 and the orphan is archived,
# the needs item is not. (Mirror the seeding helper from Task 1.)
```

- [ ] **Step 2: Run it, expect failure** — FAIL (reconcile still reads `build_port_candidates`).

- [ ] **Step 3: Implement** — in `src/mai/refresh/cycle.py`:

```python
from mai.publish.dataviz import build_port_verdicts          # replace build_port_candidates
from mai.sync.verdicts import compute_verdicts


async def reconcile_board(session) -> int:
    board = await build_port_verdicts(session)
    open_ids = {e["item_id"] for f in board["fixes"]
                for e in (*f["needs"], *f["review"])}
    repo = BoardItemRepository(session)
    archived = 0
    for item in await repo.active():
        if item.port_candidate_id not in open_ids:
            item.archived = True
            archived += 1
    return archived
```

In `run_refresh_cycle`, after `classify_subsystems(...)` and before `reconcile_board(...)`, add:

```python
await compute_verdicts(session, git_client)
await session.commit()
```

(Keep the existing `compute_port_candidates` call for now — it is offline and cheap; it is simply no longer read by the board. A follow-up cleanup removes it and `PortCandidate`.) Verify `run_refresh_cycle` already has `git_client` in scope; it is constructed in `cli/__main__._refresh` and passed in — if the signature lacks it, thread it through.

- [ ] **Step 4: Run the test + full suite** — `python -m pytest tests/test_reconcile_verdicts.py -q` → PASS, then `python -m pytest -q` → all green.

- [ ] **Step 5: Commit**

```bash
git add src/mai/refresh/cycle.py tests/test_reconcile_verdicts.py
git commit -m "feat: reconcile + refresh cycle run on PortVerdict"
```

---

### Task 4: `/port/` UI — one card per fix + matrix

**Files:**
- Rewrite: `src/mai/web/static/portboard.js`
- Modify: `src/mai/web/app.py` (the `_port_html` shell — add `#f-core`)
- Modify: `src/mai/web/static/board.css` (card-grid + matrix chips)

**Interfaces:**
- Consumes: `GET /api/board` (Task 2 shape), `POST /api/board/{item_id}/{action}` (unchanged).
- Produces: a grid of fix-cards; each card shows title/source/subsystem/tier and four matrix rows (NEEDS / REVIEW / N-A / HAS IT) of per-core chips; NEEDS & REVIEW chips are claimable/assignable; FAR review chips collapse behind a toggle; a "needs porting to [core]" `<select>` filters cards to those needing/reviewing that core.

This is a single cohesive page deliverable; validate by running the app, not unit tests.

- [ ] **Step 1: Add the core filter to the shell**

In `src/mai/web/app.py`, inside `_port_html`'s `#port-filters` block, add as the first control:

```html
<select id="f-core"><option value="">needs porting to… (any core)</option></select>
```

Keep the existing `#f-tier`, `#f-source`, `#f-subsystem`, `#f-search`, `#f-dismissed` controls.

- [ ] **Step 2: Rewrite `portboard.js`**

Replace the file with a fix-card renderer. Full content:

```javascript
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
```

(Drop the old `person` view button in `app.py` if present, or leave it inert — `applyFilters` only handles `all`/`mine`. Keep scope tight; a per-person regroup is a later enhancement.)

- [ ] **Step 3: Card-grid + chip CSS**

In `src/mai/web/static/board.css`, replace the 4-column `.port-board` rule with a card grid and add matrix styling:

```css
.port-board.fix-grid { display: grid; gap: 12px;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); }
.fcard { background: #0d1117; border: 1px solid #21262d; border-radius: 8px; padding: 10px 12px; }
.fc-top { display: flex; align-items: center; gap: 6px; font-size: 12px; color: #8b949e; }
.fc-title { font-weight: 600; margin: 4px 0; }
.fc-meta { font-size: 12px; color: #8b949e; margin-bottom: 8px; }
.mrow { display: flex; gap: 6px; align-items: baseline; margin: 3px 0; }
.mrow-lab { width: 56px; flex: none; font-size: 11px; color: #8b949e; }
.mrow-chips { display: flex; flex-wrap: wrap; gap: 4px; }
.mchip { display: inline-flex; align-items: center; gap: 4px; padding: 1px 6px;
  border-radius: 10px; font-size: 12px; border: 1px solid #30363d; }
.mchip-needs { background: #12331f; border-color: #1a7f37; color: #3fb950; }
.mchip-review.band-near { border-color: #1a7f37; }
.mchip-review.band-partial { border-color: #9a6700; }
.mchip-review { background: #1c1408; color: #d29922; }
.mchip-na { color: #6e7681; }
.mchip-has_it { color: #3fb950; }
.mchip-who { font-size: 10px; color: #58a6ff; }
.mchip-act { background: none; border: none; color: inherit; cursor: pointer; padding: 0 2px; }
.mrow-more { background: none; border: none; color: #8b949e; cursor: pointer; font-size: 11px; }
.tdot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; }
```

- [ ] **Step 4: Run the app and drive `/port/` (run skill)**

Seed the dev DB with the real five-fork verdicts (the data is already in `mai.db` from the analysis run). Start the server and load the page:

```bash
COOKIE_SECURE=false python -m uvicorn mai.web.asgi:app --port 8000 &
# log in (admin-provisioned account) and GET /port/ with the session cookie,
# or use the run-skill browser driver to log in through the form.
```

Verify (screenshot the page — look at it):
- cards render one-per-fix with the four matrix rows;
- the "needs porting to four" filter narrows to four's worklist;
- a NEEDS chip's `+` claims it (chip shows your name after);
- a REVIEW row with FAR entries shows "+N diverged" and expands on click.

Kill the server when done (`netstat -ano | grep :8000` → `taskkill //PID <pid> //F`).

- [ ] **Step 5: Commit**

```bash
git add src/mai/web/static/portboard.js src/mai/web/app.py src/mai/web/static/board.css
git commit -m "feat: /port/ re-modeled as per-fix verdict cards with core filter"
```

---

## Self-Review

- **Spec coverage:** §7 export (Task 1), §7 reconcile + cycle (Task 3), §8 UX one-card-per-fix + "needs porting to [core]" + evidence-on-chip (Task 4), §9 `build_port_verdicts` contract (Task 1) + `/api/board` (Task 2). Closeness sort (conflict-closeness spec) → Task 1 review-sort + Task 4 FAR-collapse. ✅
- **Truthfulness:** no task computes a verdict; all read `PortVerdict.verdict`. NEEDS = engine `needs` only. ✅
- **BoardItem compatibility:** every claimable entry carries `item_id = f"{pg_id}:{core}"`; `apply_action`, overlays, events, the POST route untouched. ✅
- **Type consistency:** export keys (`needs/review/na/has_it`, `item_id`, `band`, `applied/total`) match across Task 1 (producer), Task 2 (overlay merge), Task 3 (reconcile set), Task 4 (render). ✅
- **Out of scope (documented):** removing `build_port_candidates`/`PortCandidate`; the `person` view regroup; richer divergence reasons (Phase 5). ✅
- **Risk:** Task 4 has no unit test — mitigated by the run-skill smoke with explicit visual checks. Tasks 1–3 are TDD-covered and the full suite must stay green after Task 3.
