# Review Advisor P1 — Evidence Collection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** For a REVIEW item, collect — entirely offline from the local git mirrors/worktrees — the evidence a reviewer needs: the 3-way conflict (patch hunks + which rejected + the target's code there), similar commits already in the target core, and the fix's intent; expose it at `GET /api/review/{item_id}` and render it on the board's review-row expand. **No LLM in P1.**

**Architecture:** New deterministic git methods (`rejected_hunks`, `read_region`, `log_touching`) on `LocalGitClient`; a `build_review_evidence(session, git_client, item_id)` assembler in `sync/review.py` that parses the patch + reject output into per-hunk applied/rejected with target context and ranks similar commits; a session-gated `GET /api/review/{item_id}` router; and a lazy fetch on the board's review-row expand that renders the evidence panel.

**Tech Stack:** Python 3.12, async SQLAlchemy 2.0, Starlette/FastAPI, pytest (asyncio_mode=auto), vanilla JS, real-git integration tests gated on `git` being on PATH.

## Global Constraints

- **Invariant 1 — NEEDS is never touched here.** Evidence is built only for `verdict == "review"`. `build_review_evidence` MUST assert/guard this; for any other verdict it returns `None` (or an empty packet), never partial NEEDS data.
- **Deterministic + offline.** P1 makes zero network calls and zero LLM calls. Everything comes from local bare mirrors + worktrees.
- **Honest labels.** The target-code context is best-effort (the patch's line numbers are from the *source* fork). It MUST be labelled "target near line N (best-effort)" — never presented as an exact 3-way merge.
- **Reuse the optimized git path.** `rejected_hunks` uses the existing `ensure_worktree` (hot-path/dirty-tracking) + `apply --reject`; it MUST set `self._dirty.add(core)` after `--reject` (it dirties the worktree), exactly like `apply_fraction`.
- **No AI attribution** in commits (no `Co-Authored-By`, no "Generated with"). Conventional-commit style (`feat:`/`test:`).
- **4-space indent**, match the neighbouring file's style.

---

## File Structure

| File | Change | Responsibility |
|------|--------|----------------|
| `src/mai/git/client.py` | Modify | Add `rejected_hunks`, `read_region`, `log_touching` to `LocalGitClient` + the `GitClient` Protocol. |
| `src/mai/git/fake.py` | Modify | `FakeGitClient` scripted returns for the three new methods. |
| `src/mai/sync/review.py` | Create | `build_review_evidence` + pure parse/rank helpers (`_split_unified`, `_hunk_old_start`, `_rank_similar`, `_classify_type`). |
| `src/mai/web/review_api.py` | Create | `make_review_router(session_factory)` → `GET /api/review/{item_id}`. |
| `src/mai/web/app.py` | Modify | `include_router(make_review_router(...))`. |
| `src/mai/web/static/portboard.js` | Modify | Review-row `i` expand lazily fetches `/api/review/{id}` and renders the evidence panel. |
| `src/mai/web/static/board.css` | Modify | `.rev-*` evidence-panel styles. |
| `tests/test_review_git.py` | Create | Real-git tests for the three git methods. |
| `tests/test_review_evidence.py` | Create | `build_review_evidence` over a `FakeGitClient` + seeded DB. |
| `tests/test_review_api.py` | Create | `GET /api/review/{item_id}` via ASGI transport. |

---

### Task 1: Git evidence methods on `LocalGitClient`

**Files:**
- Modify: `src/mai/git/client.py`, `src/mai/git/fake.py`
- Test: `tests/test_review_git.py`

**Interfaces:**
- Produces (add to `GitClient` Protocol + `LocalGitClient` + `FakeGitClient`):
  - `async rejected_hunks(core: str, patch_text: str, paths: list[str]) -> dict[str, str]` — `{path: rej_text}` (the raw `.rej` content git wrote for each path; `""` if none).
  - `async read_region(core: str, path: str, start: int, end: int) -> str` — lines `[start, end]` (1-based, inclusive) of `HEAD:path` in the target core; `""` if the file is absent.
  - `async log_touching(core: str, paths: list[str], *, limit: int = 80) -> list[dict]` — recent non-merge commits touching any of `paths`: `[{sha, date, title}]` (sha 10-char, date `YYYY-MM-DD`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_review_git.py
import shutil, subprocess
from pathlib import Path
import pytest
from mai.git.client import LocalGitClient

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")

def _git(cwd, *a): subprocess.run(["git", *a], cwd=cwd, check=True,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def _repo(path):
    path.mkdir(); _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@t.t"); _git(path, "config", "user.name", "T")
    _git(path, "config", "commit.gpgsign", "false"); _git(path, "config", "core.autocrlf", "false")
    (path / "f.txt").write_bytes(b"a\nb\nc\nd\ne\nf\ng\nh\n")
    _git(path, "add", "f.txt"); _git(path, "commit", "-q", "-m", "base on db layer")

TWO_HUNK = ("diff --git a/f.txt b/f.txt\n--- a/f.txt\n+++ b/f.txt\n"
            "@@ -1,3 +1,4 @@\n a\n b\n+INSERTED\n c\n"
            "@@ -6,3 +7,3 @@\n WRONGF\n g\n-h\n+H\n")

async def _client(tmp_path):
    src = tmp_path / "src"; _repo(src)
    c = LocalGitClient(str(tmp_path / "mirrors"), str(tmp_path / "worktrees"))
    await c.ensure_mirror("c", src.as_uri())
    return c

async def test_rejected_hunks_returns_rej_text(tmp_path):
    c = await _client(tmp_path)
    rej = await c.rejected_hunks("c", TWO_HUNK, ["f.txt"])
    assert "f.txt" in rej
    assert "@@" in rej["f.txt"] and "WRONGF" in rej["f.txt"]   # the rejected hunk

async def test_read_region_slices_target(tmp_path):
    c = await _client(tmp_path)
    assert await c.read_region("c", "f.txt", 2, 4) == "b\nc\nd"
    assert await c.read_region("c", "nope.txt", 1, 3) == ""

async def test_log_touching_finds_commits(tmp_path):
    c = await _client(tmp_path)
    rows = await c.log_touching("c", ["f.txt"])
    assert rows and rows[0]["title"] == "base on db layer"
    assert len(rows[0]["sha"]) == 10 and len(rows[0]["date"]) == 10
    assert await c.log_touching("c", ["nope.txt"]) == []
```

- [ ] **Step 2: Run it, expect failure** — `python -m pytest tests/test_review_git.py -q` → FAIL (methods undefined).

- [ ] **Step 3: Implement on `LocalGitClient`** (in `src/mai/git/client.py`), and add the three signatures to the `GitClient` Protocol:

```python
    async def rejected_hunks(self, core: str, patch_text: str,
                             paths: list[str]) -> dict[str, str]:
        """Apply the patch with --reject; return {path: rej_text} (the hunks git
        could not place). Dirties the worktree (next ensure_worktree resets)."""
        wt = await self.ensure_worktree(core)
        await self._run_raw(["-C", wt, "apply", "--reject", "-"],
                            stdin=patch_text.encode("utf-8", "replace"))
        self._dirty.add(core)
        out: dict[str, str] = {}
        for p in paths:
            rej = Path(wt) / (p + ".rej")
            out[p] = rej.read_text("utf-8", "replace") if rej.exists() else ""
        return out

    async def read_region(self, core: str, path: str, start: int, end: int) -> str:
        """Lines [start, end] (1-based inclusive) of HEAD:path; '' if absent."""
        rc, content, _ = await self._run_raw(
            ["-C", str(self._path(core)), "show", f"HEAD:{path}"])
        if rc != 0:
            return ""
        lines = content.splitlines()
        return "\n".join(lines[max(0, start - 1):max(0, end)])

    async def log_touching(self, core: str, paths: list[str], *,
                           limit: int = 80) -> list[dict]:
        """Recent non-merge commits touching any of `paths`: [{sha, date, title}]."""
        if not paths:
            return []
        rc, out, _ = await self._run_raw(
            ["-C", str(self._path(core)), "log", "--no-merges",
             f"-n{limit}", "--format=%H%x09%cI%x09%s", "--", *paths])
        if rc != 0:
            return []
        rows: list[dict] = []
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) >= 3:
                rows.append({"sha": parts[0][:10], "date": parts[1][:10], "title": parts[2]})
        return rows
```

- [ ] **Step 4: Add `FakeGitClient` support** (`src/mai/git/fake.py`) — constructor kwargs `rejected=None`, `regions=None`, `logs=None`; methods return scripted values (defaults: `{}`, `""`, `[]`). Read the existing `FakeGitClient` and match its style. Example:

```python
    async def rejected_hunks(self, core, patch_text, paths):
        return dict(self._rejected.get((core, patch_text), {}))
    async def read_region(self, core, path, start, end):
        return self._regions.get((core, path), "")
    async def log_touching(self, core, paths, *, limit=80):
        return list(self._logs.get(core, []))
```

- [ ] **Step 5: Run tests, expect pass** — `python -m pytest tests/test_review_git.py -q` → PASS.

- [ ] **Step 6: Commit**

```bash
git add src/mai/git/client.py src/mai/git/fake.py tests/test_review_git.py
git commit -m "feat: git evidence methods (rejected_hunks, read_region, log_touching)"
```

---

### Task 2: `build_review_evidence`

**Files:**
- Create: `src/mai/sync/review.py`
- Test: `tests/test_review_evidence.py`

**Interfaces:**
- Consumes: `PortVerdict`, `Commit`, `CommitFile`, `PatchGroup` (to resolve `item_id`), `git_client.diff`, `git_client.rejected_hunks`, `git_client.read_region`, `git_client.log_touching`.
- Produces: `async build_review_evidence(session, git_client, item_id: str) -> dict | None`:
  ```json
  {"item_id": "...", "core": "...",
   "fix": {"title","body","subsystem","source_core","source_sha","magnitude","type"},
   "conflict": {"applied": int, "total": int, "band": "near|partial|far|null",
     "hunks": [{"path","applied": bool,"patch_text": str,
                "target_context": str|null,"target_line": int|null}]},
   "similar": [{"sha","date","title","score"}],
   "divergence": {"reason": str, "relevance": "portable|divergent"}}
  ```
  Returns `None` if `item_id` has no `PortVerdict` or its verdict != `"review"`.

- [ ] **Step 1: Write the failing test** (drives a `FakeGitClient`):

```python
# tests/test_review_evidence.py
from datetime import datetime, timezone
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from mai.db.base import Base
from mai.db.models import PatchGroup, Commit, CommitFile, PortVerdict
from mai.git.fake import FakeGitClient
from mai.sync.review import build_review_evidence, _rank_similar, _classify_type

PATCH = ("diff --git a/src/shared/Db.cpp b/src/shared/Db.cpp\n"
         "--- a/src/shared/Db.cpp\n+++ b/src/shared/Db.cpp\n"
         "@@ -1,3 +1,4 @@\n a\n b\n+x\n c\n"
         "@@ -6,3 +7,3 @@\n ctx\n g\n-h\n+H\n")
REJ = "@@ -6,3 +7,3 @@\n ctx\n g\n-h\n+H\n"

@pytest_asyncio.fixture
async def session():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as c: await c.run_sync(Base.metadata.create_all)
    f = async_sessionmaker(eng, expire_on_commit=False)
    async with f() as s:
        s.add(PatchGroup(id="pg1", patch_id="p1"))
        ts = datetime(2026, 6, 1, tzinfo=timezone.utc)
        cm = Commit(core="three", sha="sha123abcd", author="a", authored_at=ts,
                    committer="a", committed_at=ts,
                    message="db crash fix on shutdown\n\nFrees the cache first.")
        s.add(cm); await s.flush()
        s.add(CommitFile(commit_id=cm.id, path="src/shared/Db.cpp", subsystem="src/shared",
                         change_type="M", added_lines=2, removed_lines=1))
        s.add(PortVerdict(patch_group_id="pg1", core="four", verdict="review",
                          apply_result="conflict", relevance="portable", source_core="three",
                          source_sha="sha123abcd", subsystem="src/shared", magnitude=3,
                          tier="surgical", conflict_applied=1, conflict_total=2))
        await s.commit(); yield s

async def test_evidence_marks_rejected_hunk_with_target_context(session):
    git = FakeGitClient()
    git._diffs = {("three", "sha123abcd"): PATCH}
    git._rejected = {("four", PATCH): {"src/shared/Db.cpp": REJ}}
    git._regions = {("four", "src/shared/Db.cpp"): "g\nH-renamed\ni"}
    git._logs = {"four": [{"sha": "deadbeef00", "date": "2026-03-01",
                           "title": "rework db teardown"}]}
    ev = await build_review_evidence(session, git, "pg1:four")
    assert ev["core"] == "four"
    assert ev["fix"]["title"] == "db crash fix on shutdown"
    assert ev["fix"]["type"] == "bugfix"
    hunks = ev["conflict"]["hunks"]
    assert len(hunks) == 2
    rejected = [h for h in hunks if not h["applied"]]
    assert len(rejected) == 1 and rejected[0]["target_context"]   # context attached
    assert ev["conflict"]["applied"] == 1 and ev["conflict"]["total"] == 2
    assert ev["similar"][0]["title"] == "rework db teardown"

async def test_non_review_returns_none(session):
    # flip the verdict to needs -> no evidence
    from sqlalchemy import update
    from mai.db.models import PortVerdict as PV
    await session.execute(update(PV).values(verdict="needs"))
    await session.commit()
    assert await build_review_evidence(session, FakeGitClient(), "pg1:four") is None

def test_rank_similar_orders_by_title_overlap():
    rows = [{"sha":"a","date":"d","title":"unrelated cleanup"},
            {"sha":"b","date":"d","title":"db crash on shutdown"}]
    out = _rank_similar(rows, "db crash fix on shutdown", limit=2)
    assert out[0]["sha"] == "b" and out[0]["score"] > 0
```

> The fixture references `FakeGitClient._diffs` — confirm the attribute name the real `FakeGitClient.diff` reads and match it (read `src/mai/git/fake.py`).

- [ ] **Step 2: Run it, expect failure** — `python -m pytest tests/test_review_evidence.py -q` → FAIL (module missing).

- [ ] **Step 3: Implement `src/mai/sync/review.py`:**

```python
import re
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mai.db.models import Commit, CommitFile, PortVerdict
from mai.sync.verdicts import closeness_label

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+\d+(?:,\d+)? @@", re.M)
_DOC = ("doc", "docs", "comment", "readme", "changelog")
_BUILD = ("cmake", "build", "ci", "makefile", ".yml", ".yaml")
_REFACTOR = ("refactor", "rename", "cleanup", "style", "format", "move")
_STOP = {"the", "a", "an", "to", "of", "in", "on", "for", "and", "fix", "fixes",
         "fixed", "add", "added", "update", "updated", "support", "by"}


def _classify_type(title: str, paths: list[str]) -> str:
    t = title.lower()
    blob = t + " " + " ".join(paths).lower()
    if any(k in blob for k in _DOC):
        return "docs"
    if any(k in blob for k in _BUILD):
        return "build"
    if any(k in t for k in _REFACTOR):
        return "refactor"
    if any(k in t for k in ("crash", "bug", "leak", "wrong", "incorrect", "null", "deadlock")):
        return "bugfix"
    return "change"


def _tokens(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{3,}", s.lower()) if w not in _STOP}


def _rank_similar(rows: list[dict], title: str, *, limit: int = 3) -> list[dict]:
    want = _tokens(title)
    out = []
    for r in rows:
        have = _tokens(r["title"])
        inter = want & have
        score = round(len(inter) / len(want | have), 2) if (want | have) else 0.0
        if score > 0:
            out.append({**r, "score": score})
    out.sort(key=lambda r: -r["score"])
    return out[:limit]


def _split_unified(patch: str) -> dict[str, list[str]]:
    """diff -> {path: [hunk_text, ...]} keyed by the b/ path of each file block."""
    files: dict[str, list[str]] = {}
    path, buf = None, []
    def flush():
        if path is not None and buf:
            files.setdefault(path, []).append("\n".join(buf))
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            flush(); buf = []; path = None
        elif line.startswith("+++ b/"):
            path = line[6:].strip()
        elif line.startswith("@@ "):
            flush(); buf = [line]
        elif buf:
            buf.append(line)
    flush()
    return files


def _hunk_old_start(hunk_text: str) -> int | None:
    m = _HUNK_RE.search(hunk_text)
    return int(m.group(1)) if m else None


def _hunk_header(hunk_text: str) -> str:
    return hunk_text.splitlines()[0] if hunk_text else ""


async def build_review_evidence(session: AsyncSession, git_client, item_id: str) -> dict | None:
    pg_id, _, core = item_id.rpartition(":")
    v = await session.scalar(select(PortVerdict).where(
        PortVerdict.patch_group_id == pg_id, PortVerdict.core == core))
    if v is None or v.verdict != "review":
        return None
    commit = await session.scalar(select(Commit).where(
        Commit.core == v.source_core, Commit.sha == v.source_sha))
    msg = (commit.message if commit else "") or ""
    title = msg.strip().splitlines()[0] if msg.strip() else item_id
    body = "\n".join(msg.strip().splitlines()[1:]).strip()
    files = list(await session.scalars(select(CommitFile).where(
        CommitFile.commit_id == (commit.id if commit else None))))
    paths = sorted({f.path for f in files})

    patch = await git_client.diff(v.source_core, v.source_sha)
    per_file = _split_unified(patch)
    rej_map = await git_client.rejected_hunks(core, patch, paths)
    rej_headers = {p: {_hunk_header(h) for h in _split_rej(rt)}
                   for p, rt in rej_map.items()}

    hunks, total, applied = [], 0, 0
    for p, hlist in per_file.items():
        for h in hlist:
            total += 1
            is_rej = _hunk_header(h) in rej_headers.get(p, set())
            tgt, tline = None, None
            if is_rej:
                tline = _hunk_old_start(h)
                if tline is not None:
                    tgt = await git_client.read_region(core, p, max(1, tline - 3), tline + 6) or None
            else:
                applied += 1
            hunks.append({"path": p, "applied": not is_rej, "patch_text": h,
                          "target_context": tgt, "target_line": tline})

    band = (closeness_label(v.conflict_applied, v.conflict_total)
            if v.conflict_total else None)
    similar = _rank_similar(await git_client.log_touching(core, paths), title)
    return {
        "item_id": item_id, "core": core,
        "fix": {"title": title, "body": body, "subsystem": v.subsystem,
                "source_core": v.source_core, "source_sha": v.source_sha,
                "magnitude": v.magnitude, "type": _classify_type(title, paths)},
        "conflict": {"applied": applied, "total": total, "band": band, "hunks": hunks},
        "similar": similar,
        "divergence": {"reason": (v.evidence or [""])[1] if v.evidence else "",
                       "relevance": v.relevance}}


def _split_rej(rej_text: str) -> list[str]:
    """Split a .rej file into individual hunk texts."""
    if not rej_text.strip():
        return []
    parts, cur = [], []
    for line in rej_text.splitlines():
        if line.startswith("@@ "):
            if cur:
                parts.append("\n".join(cur))
            cur = [line]
        elif cur:
            cur.append(line)
    if cur:
        parts.append("\n".join(cur))
    return parts
```

- [ ] **Step 4: Run the tests, expect pass** — `python -m pytest tests/test_review_evidence.py -q` → PASS. Then the full suite stays green: `python -m pytest -q`.

- [ ] **Step 5: Commit**

```bash
git add src/mai/sync/review.py tests/test_review_evidence.py
git commit -m "feat: build_review_evidence — 3-way conflict + similar commits + intent"
```

---

### Task 3: `GET /api/review/{item_id}`

**Files:**
- Create: `src/mai/web/review_api.py`
- Modify: `src/mai/web/app.py`
- Test: `tests/test_review_api.py`

**Interfaces:**
- Consumes: `build_review_evidence`, `LocalGitClient(settings.git_mirror_dir)`, session gate.
- Produces: `GET /api/review/{item_id}` → `{"evidence": <packet>|null}` (200; `null` when the item isn't a review). Mirror `make_me_router`'s structure (read `src/mai/web/me_api.py`).

- [ ] **Step 1: Write the failing test** — mirror `tests/test_review_api.py` on the existing board-api test's ASGI + login fixture; seed a review `PortVerdict` (+ PatchGroup/Commit/CommitFile as in Task 2); use a `FakeGitClient`-injected app **or** assert the real-git path is skipped when git is absent. Minimal assertion: logged-in `GET /api/review/pg1:four` → 200 and body has key `evidence` (object or null); unauthenticated → 303 to `/login`.

> Read how `make_me_router` is constructed and injected in `create_app`; the review router takes `session_factory` and builds its own `LocalGitClient` from `settings`, exactly like `_sync_analyze`. For the test, if constructing a real `LocalGitClient` is awkward, factor the git client as a parameter with a `LocalGitClient` default so the test can pass a `FakeGitClient`.

- [ ] **Step 2: Run it, expect failure.**

- [ ] **Step 3: Implement `src/mai/web/review_api.py`:**

```python
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from mai.config import settings
from mai.git.client import LocalGitClient
from mai.sync.review import build_review_evidence


def make_review_router(session_factory, git_client=None) -> APIRouter:
    """GET /api/review/{item_id} — deterministic evidence for one REVIEW item."""
    router = APIRouter(prefix="/api/review")
    client = git_client or LocalGitClient(settings.git_mirror_dir)

    @router.get("/{item_id}")
    async def get_review(request: Request, item_id: str):
        async with session_factory() as session:
            evidence = await build_review_evidence(session, client, item_id)
        return JSONResponse({"evidence": evidence})

    return router
```

Wire it in `app.py` beside the others: `app.include_router(make_review_router(session_factory))` and the import.

- [ ] **Step 4: Run the test + full suite** — both green.

- [ ] **Step 5: Commit**

```bash
git add src/mai/web/review_api.py src/mai/web/app.py tests/test_review_api.py
git commit -m "feat: GET /api/review/{item_id} serves deterministic review evidence"
```

---

### Task 4: Board review-row evidence panel

**Files:**
- Modify: `src/mai/web/static/portboard.js`, `src/mai/web/static/board.css`

**Interfaces:**
- Consumes: `GET /api/review/{item_id}` (Task 3).
- Produces: clicking the review row's `i` lazily fetches the evidence and renders the panel (intent · 3-way hunks with rejected highlighted + target context · similar commits). Validate by running the app (run skill), not unit tests.

- [ ] **Step 1: Replace the review row's static proof with a lazy evidence fetch.** In `portboard.js`, change the `[data-why]` click handler so that for a **review** row it fetches `/api/review/${id}` once (cache on the element), renders into the `.t-proof` container, and toggles it. Full handler edit:

```javascript
  const why = e.target.closest("[data-why]");
  if (why) {
    const row = why.closest(".task"), proof = row.querySelector(".t-proof");
    if (proof.dataset.loaded || !row.classList.contains("t-review")) {
      proof.hidden = !proof.hidden; return;
    }
    proof.hidden = false; proof.innerHTML = "<div class='rev-load'>collecting evidence…</div>";
    fetch(`/api/review/${encodeURIComponent(row.dataset.id)}`)
      .then(r => r.json()).then(j => {
        proof.dataset.loaded = "1";
        proof.innerHTML = j.evidence ? renderEvidence(j.evidence)
          : "<div class='rev-load'>no evidence (not a review item)</div>";
      });
    return;
  }
```

- [ ] **Step 2: Add `renderEvidence(ev)` to `portboard.js`:**

```javascript
function hunkBlock(h) {
  const cls = h.applied ? "rev-hunk applied" : "rev-hunk rejected";
  const tgt = h.target_context
    ? `<div class="rev-tgt">target near line ${h.target_line} (best-effort):
        <pre>${esc(h.target_context)}</pre></div>` : "";
  return `<div class="${cls}"><div class="rev-hh">${h.applied ? "✓ applies" : "✗ rejects"} · ${esc(h.path)}</div>
    <pre>${esc(h.patch_text)}</pre>${tgt}</div>`;
}
function renderEvidence(ev) {
  const f = ev.fix, c = ev.conflict;
  const sim = ev.similar.length
    ? `<div class="rev-sec"><b>Already in ${cap(ev.core)}?</b> ${ev.similar.map(s =>
        `<div class="rev-sim">~${Math.round(s.score*100)}% · <code>${esc(s.sha)}</code>
         ${esc(s.title)} <span class="rev-d">${esc(s.date)}</span></div>`).join("")}</div>`
    : `<div class="rev-sec rev-muted">no similar commits found in ${cap(ev.core)}</div>`;
  return `<div class="rev">
    <div class="rev-sec"><b>What it does</b> · ${esc(f.type)} · ${esc(f.subsystem)} · ${f.magnitude} lines
      ${f.body ? `<div class="rev-body">${esc(f.body.slice(0,300))}</div>` : ""}</div>
    <div class="rev-sec"><b>Why review</b> · ${c.applied}/${c.total} hunks apply
      ${c.hunks.map(hunkBlock).join("")}</div>
    ${sim}</div>`;
}
```

- [ ] **Step 3: Add `.rev-*` CSS** to `board.css` (monospace `pre`, green-left for applied, red-left for rejected hunks, target-context box, similar-commit rows). Keep the light theme.

- [ ] **Step 4: Validate with the run skill** — start `COOKIE_SECURE=false python -m mai.cli serve-web`, log in, expand a review row, confirm: intent line, hunks (rejected ones flagged + target context shown), similar-commit list. Screenshot and look at it. `node --check src/mai/web/static/portboard.js` must pass. Kill the server when done.

- [ ] **Step 5: Commit**

```bash
git add src/mai/web/static/portboard.js src/mai/web/static/board.css
git commit -m "feat: review-row evidence panel (3-way conflict + similar commits)"
```

---

## Self-Review

- **Spec coverage (P1 of `review-advisor.md` §9):** git methods + `build_review_evidence` (Tasks 1–2), `GET /api/review` (Task 3), the evidence panel — 3-way view + similar + intent (Task 4). The LLM/judge/guardrail is explicitly **P2, not here**. ✅
- **Invariant 1:** `build_review_evidence` returns `None` for non-review verdicts (Task 2 test `test_non_review_returns_none`); the endpoint returns `{"evidence": null}`. ✅
- **Type consistency:** the evidence keys produced in Task 2 (`fix/conflict/hunks/similar/divergence`, `applied`, `target_context`, `target_line`, `score`) are exactly what Task 4 renders and Task 3 passes through. ✅
- **Offline:** no network/LLM anywhere in P1; only local git + DB. ✅
- **Honesty:** target context is labelled "best-effort" in both the data intent and the UI. ✅
- **Reuse:** `rejected_hunks` reuses `ensure_worktree` + sets `_dirty` like `apply_fraction`; the router mirrors `make_me_router`. ✅
- **Risk:** Task 4 has no unit test — mitigated by the run-skill smoke + `node --check`. The patch/.rej hunk-matching (by header line) is the trickiest bit; Task 2's real-data-shaped fake test covers the rejected-vs-applied split.
