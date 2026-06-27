# L2 Code-Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cache the tree-sitter parses the classifier repeats over target worktrees — a `CodeFileIndex` keyed `(core, base_sha, path)` and a `get_file_index` lookup that returns a `FileIndex` (a faithful drop-in for `cppindex.extract`'s target-side calls) — and wire the classifier through it, behavior-preserving.

**Architecture:** `FileIndex` reconstructs `file_symbols`/`find_function` from cached JSON instead of re-parsing. `get_file_index(session, …)` returns a cache hit, else reads+parses+adds a row (no commit — the caller's transaction owns the commit; autoflush makes within-run lookups hit). `session=None` or any cache fault → direct parse, no persistence. `missing_in_file` takes a `FileIndex` instead of `target_bytes`; `classify_from_apply` resolves it via `get_file_index`. Phase 2 of `docs/specs/code-memory-l2-design.md`.

**Tech Stack:** Python 3.12, async SQLAlchemy 2.0, tree-sitter (existing `cppindex`), pytest. No new deps.

## Global Constraints

- **Behavior-preserving** — the classifier's `Verdict` output is identical with the index on or off. The `#229` acceptance golden and all `test_portability_*` must pass unchanged.
- **Derived & recomputable** — keyed on `(core, base_sha, path)`; a fork HEAD move re-indexes. Cache-only.
- **Best-effort** — `session=None`, a missing `code_file_index` table, or any cache I/O fault degrades to a direct parse (or `exists=False` for an absent file); it NEVER makes the classifier raise.
- **No mid-batch commit** — `get_file_index` only `session.add`s; it never commits or flushes explicitly. The caller (`compute_verdicts`) commits once at the end; autoflush makes a within-run second lookup of the same key a cache hit.
- **Fidelity** — `FileIndex.find_function(name)` / `.file_symbols` return exactly what `extract.find_function(bytes, name)` / `extract.file_symbols(bytes)` would for the same content.
- New table via `create_all` + one-time `mai-data/tmp/` create script. No AI attribution; conventional commits; 4-space indent.

---

## File Structure

| File | Change | Responsibility |
|------|--------|----------------|
| `src/mai/db/models.py` | Modify | add `CodeFileIndex` model |
| `src/mai/codememory/__init__.py` | Create | package marker |
| `src/mai/codememory/index.py` | Create | `FileIndex` + `get_file_index` + JSON (de)serialize |
| `mai-data/tmp/create_code_file_index.py` | Create | one-time live-DB table create (gitignored) |
| `src/mai/portability/symbols.py` | Modify | `missing_in_file`: `target_bytes` → `target_index: FileIndex \| None` |
| `src/mai/portability/classifier.py` | Modify | `classify_from_apply(session=None)` resolves `target_index` via `get_file_index` |
| `src/mai/sync/verdicts.py` | Modify | `compute_verdicts` passes its `session` to `classify_from_apply` |
| `tests/test_codememory.py` | Create | model + get_file_index (hit/miss/HEAD-move/fidelity/no-session) |
| `tests/test_portability_symbols.py` | Modify | `missing_in_file` now takes a `FileIndex` |

---

### Task 1: `CodeFileIndex` model

**Files:**
- Modify: `src/mai/db/models.py`
- Create: `mai-data/tmp/create_code_file_index.py`
- Test: `tests/test_codememory.py` (model portion)

**Interfaces:**
- Produces: `CodeFileIndex` (table `code_file_index`, unique `(core, base_sha, path)`; cols `exists` Bool, `file_symbols` JSON, `functions` JSON, `indexed_at`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_codememory.py
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mai.db.base import Base
from mai.db.models import CodeFileIndex


@pytest_asyncio.fixture
async def cm_session():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(eng, expire_on_commit=False)() as s:
        yield s


async def test_code_file_index_roundtrips(cm_session):
    cm_session.add(CodeFileIndex(
        core="zero", base_sha="b1", path="src/x.cpp", exists=True,
        file_symbols=["A", "B"],
        functions=[{"name": "f", "qualified_name": "C::f", "start_line": 1,
                    "end_line": 9, "params": ["x"], "locals": ["y"]}]))
    await cm_session.commit()
    row = await cm_session.scalar(select(CodeFileIndex).where(
        CodeFileIndex.core == "zero", CodeFileIndex.base_sha == "b1",
        CodeFileIndex.path == "src/x.cpp"))
    assert row.exists is True and row.file_symbols == ["A", "B"]
    assert row.functions[0]["name"] == "f" and row.functions[0]["params"] == ["x"]
```

- [ ] **Step 2: Run it, expect failure** — `python -m pytest tests/test_codememory.py -q` → FAIL (`CodeFileIndex` undefined).

- [ ] **Step 3: Implement the model** in `src/mai/db/models.py` (beside `ReviewAdvice`; `Boolean`/`JSON`/`String`/`Text` are already imported, plus `_uuid`/`_now`):

```python
class CodeFileIndex(Base):
    """Cached tree-sitter extraction for one file at one fork HEAD. Derived &
    recomputable: keyed (core, base_sha, path); recomputed when the fork HEAD moves.
    Cache-only — safe to drop/rebuild."""
    __tablename__ = "code_file_index"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    core: Mapped[str] = mapped_column(String(64))
    base_sha: Mapped[str] = mapped_column(String(40))
    path: Mapped[str] = mapped_column(Text)
    exists: Mapped[bool] = mapped_column(Boolean, default=True)
    file_symbols: Mapped[list] = mapped_column(JSON, default=list)
    functions: Mapped[list] = mapped_column(JSON, default=list)
    indexed_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)

    __table_args__ = (
        UniqueConstraint("core", "base_sha", "path", name="uq_code_file_index"),
    )
```

- [ ] **Step 4: Run test, expect pass** — `python -m pytest tests/test_codememory.py -q` → PASS.

- [ ] **Step 5: Create `mai-data/tmp/create_code_file_index.py`** (idempotent live-DB create):

```python
"""Create the code_file_index cache table on the live mai.db (create_all is idempotent)."""
import asyncio
from mai.db.base import Base
from mai.db.session import engine
import mai.db.models  # noqa: F401


async def main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("OK: ensured code_file_index exists")


asyncio.run(main())
```

- [ ] **Step 6: Full suite** — `python -m pytest -q` → green.

- [ ] **Step 7: Commit**

```bash
git add src/mai/db/models.py tests/test_codememory.py mai-data/tmp/create_code_file_index.py
git commit -m "feat: CodeFileIndex cache table (per-file tree-sitter extraction)"
```

---

### Task 2: `FileIndex` + `get_file_index`

**Files:**
- Create: `src/mai/codememory/__init__.py`, `src/mai/codememory/index.py`
- Test: `tests/test_codememory.py`

**Interfaces:**
- Consumes: `CodeFileIndex` (Task 1), `cppindex.extract` (`functions`, `file_symbols`, `CppFunction`), `git_client.read_file(core, ref, path) -> str | None`.
- Produces:
  - `FileIndex(exists: bool, file_symbols: set[str], functions: list[CppFunction])` with `find_function(name) -> CppFunction | None` (first `fn.name == name`).
  - `async get_file_index(session, git_client, core, base_sha, path) -> FileIndex`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_codememory.py`):

```python
from mai.cppindex import extract
from mai.cppindex.extract import CppFunction
from mai.git.fake import FakeGitClient
from mai.codememory.index import FileIndex, get_file_index

CPP = (b"namespace ai {\n"
       b"int helper(int a, int b) { int t = a; return t + b; }\n"
       b"class Foo {\n public:\n  void run(char* p) { int q = 0; }\n};\n"
       b"}\n")


def _git_with_file(core, ref, path, content):
    g = FakeGitClient(files={(core, ref, path): content})
    return g


async def test_get_file_index_miss_parses_and_caches(cm_session, monkeypatch):
    calls = {"n": 0}
    real = extract.functions
    def spy(src):
        calls["n"] += 1
        return real(src)
    monkeypatch.setattr("mai.codememory.index.extract.functions", spy)

    git = _git_with_file("zero", "b1", "src/x.cpp", CPP.decode())
    idx1 = await get_file_index(cm_session, git, "zero", "b1", "src/x.cpp")
    assert idx1.exists and "helper" in idx1.file_symbols
    assert idx1.find_function("helper").params == ["a", "b"]
    assert calls["n"] == 1
    # second lookup, same key -> cache hit, NO re-parse
    idx2 = await get_file_index(cm_session, git, "zero", "b1", "src/x.cpp")
    assert idx2.find_function("helper").params == ["a", "b"]
    assert calls["n"] == 1                       # still 1 — served from cache


async def test_get_file_index_reindexes_when_head_moves(cm_session):
    git = FakeGitClient(files={("zero", "b1", "src/x.cpp"): CPP.decode(),
                               ("zero", "b2", "src/x.cpp"): "int only() { return 0; }\n"})
    a = await get_file_index(cm_session, git, "zero", "b1", "src/x.cpp")
    b = await get_file_index(cm_session, git, "zero", "b2", "src/x.cpp")
    assert a.find_function("helper") is not None
    assert b.find_function("helper") is None and b.find_function("only") is not None


async def test_get_file_index_fidelity_matches_extract(cm_session):
    git = _git_with_file("zero", "b1", "src/x.cpp", CPP.decode())
    idx = await get_file_index(cm_session, git, "zero", "b1", "src/x.cpp")
    assert idx.file_symbols == extract.file_symbols(CPP)
    assert idx.find_function("run").params == extract.find_function(CPP, "run").params


async def test_get_file_index_absent_file(cm_session):
    git = FakeGitClient(files={})                # read_file -> None
    idx = await get_file_index(cm_session, git, "zero", "b1", "nope.cpp")
    assert idx.exists is False and idx.file_symbols == set()
    assert idx.find_function("anything") is None


async def test_get_file_index_no_session_parses_directly(cm_session):
    git = _git_with_file("zero", "b1", "src/x.cpp", CPP.decode())
    idx = await get_file_index(None, git, "zero", "b1", "src/x.cpp")   # no cache
    assert idx.find_function("helper").params == ["a", "b"]
    n = await cm_session.scalar(select(__import__("sqlalchemy").func.count()).select_from(CodeFileIndex))
    assert n == 0                                # nothing persisted without a session
```

> Confirm `FakeGitClient(files=...)` is the real constructor kwarg for scripted `read_file` (read `src/mai/git/fake.py`); if the attribute differs, match it. Use the clean `from sqlalchemy import func` form for the count rather than `__import__`.

- [ ] **Step 2: Run them, expect failure** — module `mai.codememory.index` missing.

- [ ] **Step 3: Implement `src/mai/codememory/__init__.py`** (empty) and `src/mai/codememory/index.py`:

```python
from dataclasses import dataclass, field

from sqlalchemy import select

from mai.cppindex import extract
from mai.cppindex.extract import CppFunction
from mai.db.models import CodeFileIndex


@dataclass
class FileIndex:
    exists: bool
    file_symbols: set[str] = field(default_factory=set)
    functions: list[CppFunction] = field(default_factory=list)

    def find_function(self, name: str) -> CppFunction | None:
        for fn in self.functions:        # tree order — mirrors extract.find_function
            if fn.name == name:
                return fn
        return None


def _fn_to_dict(fn: CppFunction) -> dict:
    return {"name": fn.name, "qualified_name": fn.qualified_name,
            "start_line": fn.start_line, "end_line": fn.end_line,
            "params": list(fn.params), "locals": sorted(fn.locals)}


def _fn_from_dict(d: dict) -> CppFunction:
    return CppFunction(name=d["name"], qualified_name=d["qualified_name"],
                       start_line=d["start_line"], end_line=d["end_line"],
                       params=list(d["params"]), locals=set(d["locals"]))


async def get_file_index(session, git_client, core: str, base_sha: str, path: str) -> FileIndex:
    """Cached tree-sitter extraction for (core, base_sha, path). Cache hit -> reconstruct;
    miss -> read+parse and add a row (NO commit — the caller's transaction owns it; autoflush
    makes a within-run repeat lookup a hit). session=None or any cache fault -> direct parse,
    no persistence. Never raises for cache reasons."""
    cache_ok = session is not None
    if cache_ok:
        try:
            row = await session.scalar(select(CodeFileIndex).where(
                CodeFileIndex.core == core, CodeFileIndex.base_sha == base_sha,
                CodeFileIndex.path == path))
        except Exception:  # noqa: BLE001 — missing table / cache fault -> parse directly
            row, cache_ok = None, False
        else:
            if row is not None:
                return FileIndex(
                    exists=row.exists, file_symbols=set(row.file_symbols),
                    functions=[_fn_from_dict(d) for d in row.functions])

    text = await git_client.read_file(core, base_sha, path)
    if text is None:
        idx = FileIndex(exists=False)
        fns_json, syms_json = [], []
    else:
        b = text.encode("utf-8", "replace")
        funcs = extract.functions(b)
        syms = extract.file_symbols(b)
        idx = FileIndex(exists=True, file_symbols=syms, functions=funcs)
        fns_json = [_fn_to_dict(f) for f in funcs]
        syms_json = sorted(syms)

    if cache_ok:
        try:
            session.add(CodeFileIndex(
                core=core, base_sha=base_sha, path=path, exists=idx.exists,
                file_symbols=syms_json, functions=fns_json))
        except Exception:  # noqa: BLE001 — never break the caller over a cache write
            pass
    return idx
```

- [ ] **Step 4: Run the codememory tests + full suite** — both green.

- [ ] **Step 5: Commit**

```bash
git add src/mai/codememory/__init__.py src/mai/codememory/index.py tests/test_codememory.py
git commit -m "feat: code-memory get_file_index — cached FileIndex over target worktrees"
```

---

### Task 3: `missing_in_file` takes a `FileIndex`

**Files:**
- Modify: `src/mai/portability/symbols.py`
- Test: `tests/test_portability_symbols.py`

**Interfaces:**
- Consumes: `FileIndex` (Task 2).
- Produces: `missing_in_file(*, source_bytes, target_index: FileIndex | None, added_lines, enclosing_name=None)` — the `target_bytes` param is replaced. `None` or `not target_index.exists` → `[]`. Target-side `find_function`/`file_symbols` come from the index. Source side unchanged.

- [ ] **Step 1: Update the failing tests** — in `tests/test_portability_symbols.py`, the existing tests pass `target_bytes=...`. Change them to pass a `FileIndex` built from the same bytes, so the gate logic is unchanged but driven through the index. Read the file first; for each call site, replace:

```python
# was: target_bytes=TARGET_BYTES
# now:
from mai.cppindex import extract
from mai.codememory.index import FileIndex
def _index(b):
    return FileIndex(exists=True, file_symbols=extract.file_symbols(b),
                     functions=extract.functions(b))
...
missing_in_file(source_bytes=SRC, target_index=_index(TGT), added_lines=LINES)
# and the "absent target" case: target_index=None  (or FileIndex(exists=False))
```

Keep every assertion identical (e.g. the #229 case: target lacking `loc` → one MissingSymbol naming `loc`). Add one case asserting `target_index=FileIndex(exists=False)` returns `[]`.

- [ ] **Step 2: Run, expect failure** — `missing_in_file` still wants `target_bytes`.

- [ ] **Step 3: Implement** — edit `src/mai/portability/symbols.py`:
  1. Add `from __future__ import annotations` is already present; add an import guard for the type only (avoid a hard import cycle): at top, under `from dataclasses import dataclass`, add `from mai.codememory.index import FileIndex` (codememory imports cppindex + db.models, not portability — no cycle; confirm).
  2. Change the signature and the target-side block:

```python
def missing_in_file(
    *,
    source_bytes: bytes,
    target_index: "FileIndex | None",
    added_lines: set[int],
    enclosing_name: str | None = None,
) -> list[MissingSymbol]:
    if target_index is None or not target_index.exists:
        return []

    referenced = extract.identifiers_on_lines(source_bytes, added_lines)
    introduced = extract.declared_on_lines(source_bytes, added_lines)
    free = referenced - introduced
    if not free:
        return []

    src_fn = (extract.find_function(source_bytes, enclosing_name) if enclosing_name
              else extract.function_covering(source_bytes, added_lines))
    src_scope = src_fn.scope_names if src_fn else set()
    src_syms = extract.file_symbols(source_bytes)

    candidates = {n for n in free if _resolves(n, src_scope, src_syms)}
    if not candidates:
        return []

    tgt_fn = (target_index.find_function(src_fn.name) if src_fn
              else (target_index.find_function(enclosing_name) if enclosing_name else None))
    tgt_scope = tgt_fn.scope_names if tgt_fn else set()
    tgt_syms = target_index.file_symbols
    # ... the rest of the loop is unchanged (uses tgt_scope / tgt_syms) ...
```

Leave `_resolves`, `_origin`, `MissingSymbol`, and the `missing`-building loop exactly as they are.

- [ ] **Step 4: Run the symbols tests + full suite** — green.

- [ ] **Step 5: Commit**

```bash
git add src/mai/portability/symbols.py tests/test_portability_symbols.py
git commit -m "refactor: Gate 3 takes a FileIndex (code-memory) for the target side"
```

---

### Task 4: Wire the classifier through the index

**Files:**
- Modify: `src/mai/portability/classifier.py`, `src/mai/sync/verdicts.py`
- Test: existing `tests/test_portability_classifier.py` + `tests/test_acceptance_portability.py` (must pass unchanged)

**Interfaces:**
- Consumes: `get_file_index` (Task 2), `missing_in_file(target_index=…)` (Task 3).
- Produces: `classify_from_apply(git_client, *, …, session=None)` resolves `target_index` via `get_file_index` and passes it to `missing_in_file`. `evaluate` passes `session=None`; `compute_verdicts` passes its `session`.

- [ ] **Step 1: Confirm the golden tests are green BEFORE editing** — `python -m pytest tests/test_portability_classifier.py tests/test_acceptance_portability.py -q`. Note the result (this is the behavior-preservation baseline). If `test_acceptance_portability` skips (mirrors absent), note that.

- [ ] **Step 2: Edit `src/mai/portability/classifier.py`:**
  1. Add import: `from mai.codememory.index import get_file_index`.
  2. Add `session=None` to `classify_from_apply`'s keyword params.
  3. Replace the target-read block in the Gate-3 loop:

```python
        source_text = await git_client.read_file(source_core, source_sha, path)
        if source_text is None:
            continue
        target_index = await get_file_index(session, git_client, target_core, target_head, path)
        for ms in missing_in_file(
                source_bytes=source_text.encode("utf-8", "replace"),
                target_index=target_index,
                added_lines=fp.added_lines):
            missing.append(f"{path}: required symbol {ms.detail}")
```

  4. In `evaluate`, pass `session=None` explicitly in its `classify_from_apply(...)` call (keeps it self-contained / cache-free).

- [ ] **Step 3: Edit `src/mai/sync/verdicts.py`** — in `compute_verdicts`, pass the session to the call at ~line 110:

```python
                sv = await classify_from_apply(
                    git_client, patch=patch, apply_result=apply_result,
                    source_core=source_core, source_sha=source_sha,
                    target_core=target_core, target_head=base, session=session)
```

- [ ] **Step 4: Run the golden + full suite** — `python -m pytest tests/test_portability_classifier.py tests/test_acceptance_portability.py -q` → SAME result as Step 1 (identical verdicts; #229 still NOT_APPLICABLE naming `loc`). Then `python -m pytest -q` → full suite green.

- [ ] **Step 5: Commit**

```bash
git add src/mai/portability/classifier.py src/mai/sync/verdicts.py
git commit -m "feat: classifier Gate 3 reads target symbols from code-memory cache"
```

---

## Self-Review

- **Spec coverage (`code-memory-l2-design.md`):** model (T1) · FileIndex + get_file_index best-effort/recomputable (T2) · missing_in_file refactor (T3) · classifier wiring + golden (T4). ✅
- **Behavior-preserving:** T3/T4 keep all classifier assertions; T4 Step 1/4 diff the golden before/after. ✅
- **No mid-batch commit:** `get_file_index` only `add`s; `compute_verdicts` still commits once. ✅
- **Best-effort:** session=None and SELECT-fault both fall to direct parse; absent file → `exists=False`. ✅ (tested)
- **Fidelity:** T2 asserts `FileIndex` == `extract.*` on the same bytes; reconstruct order preserved (tree order) so `find_function`/`function_covering` "first/most" semantics hold. ✅
- **No import cycle:** `codememory.index` imports `cppindex` + `db.models`; `symbols.py` imports `codememory.index`; `codememory` does NOT import `portability`. ✅ (T3 Step 3 confirms)
- **Placeholder scan:** clean; the only ops step is the live-DB create (T1 Step 5).

## Execution Handoff

Recommended: **subagent-driven-development** (fresh implementer + task review per task, final whole-branch review), same as P1/P2/P3.
