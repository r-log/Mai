# Mai Sync Engine — Phase 1: Git-Worker Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land **code truth** in the DB — harvest commits from each tracked fork via a real git layer, recording per-commit metadata, touched files (+ line magnitude + renames), and the `git patch-id`, idempotently and incrementally.

**Architecture:** A `GitClient` protocol abstracts git so orchestration is testable without clones: `FakeGitClient` (in-memory `CommitMeta` fixtures) for unit tests, `LocalGitClient` (async subprocess over bare `--mirror` clones) for production. `commits_harvest_repo` orchestrates: ensure mirror → fetch → list new commits since a SHA cursor → persist via `CommitRepository` → advance cursor. Patch-ids are *captured* here; cross-fork patch *matching* (PatchGroup/Propagation) is Phase 2.

**Tech Stack:** Python 3.12 · `asyncio.create_subprocess_exec` (git via subprocess, no new dep) · SQLAlchemy 2.0 async · pytest + pytest-asyncio · real `git` on PATH for the one integration test (skipped if absent).

## Global Constraints

Copied verbatim from `docs/specs/sync-intelligence-engine.md`:
- **Read-only externally.** This phase only reads git; Mai writes nothing to GitHub/IPS.
- **Raw is append-only & immutable; everything else recomputable.** `Commit / CommitFile / CommitPatch` are raw code-truth.
- **Git vouches, we don't approximate.** Patch identity = `git patch-id --stable`, never a hand-rolled diff hash (the `normalized_hash` column stays null this phase; it's a labeled Phase-2 fallback).
- **Commit-anchored.** The git cursor is a commit **SHA**, not a timestamp.
- **Install-target-agnostic.** Tracked repos come from the `Repo` registry (data), never hard-coded.
- **Match the stack:** async SQLAlchemy 2.0, `Fake*` protocol seams, repository seam for all DB access, 4-space indent, `feat:`-style commit messages, **no AI attribution in commits**.

---

## Builds on existing code

These already exist and MUST be reused as-is (do not redefine):
- `mai.db.base.Base`, and `mai.db.models` helpers `_uuid`, `_now`; existing columns use `String/Text/Integer/Boolean/JSON/ForeignKey/UniqueConstraint` (all already imported in `models.py`).
- `mai.db.models.Repo(full_name, core, url)` — the tracked-repo registry; `RepoRepository(session).all()`.
- `mai.db.models.SyncCursor(repo_full_name, source_type, last_updated_at)` + `mai.repository.cursors.CursorRepository` with `get(repo, source_type)` / `set(repo, source_type, value)`. Reused for the git cursor with `source_type="git_commit"`, `last_updated_at`=last harvested commit SHA (opaque string; the field name is timestamp-flavored but holds a SHA here).
- `mai.drift.compare.subsystem_of(path, depth=3)` — reused to tag `CommitFile.subsystem`.
- `tests/conftest.py` — the async in-memory sqlite `session` fixture (runs `Base.metadata.create_all`; test modules import the new models so they register).
- CLI pattern in `mai/src/mai/cli/__main__.py` (subparser + `async def _cmd()` + dispatch).

## File Structure

```
src/mai/
  config.py                    # MODIFY: add git_mirror_dir
  db/models.py                 # MODIFY: add Commit, CommitFile, CommitPatch
  git/
    __init__.py                # new (empty)
    types.py                   # CommitMeta, CommitFileMeta dataclasses
    client.py                  # GitClient protocol + GitError + LocalGitClient
    fake.py                    # FakeGitClient (tests)
  repository/commits.py        # CommitRepository (seam)
  git_harvest.py               # commits_harvest_repo orchestration
  cli/__main__.py              # MODIFY: add commits-harvest subcommand
tests/
  test_git_fake.py
  test_commit_repository.py
  test_git_harvest.py
  test_local_git_client.py     # integration (real git, skipped if absent)
```

---

### Task 1: Git value types + `GitClient` protocol + `FakeGitClient`

**Files:**
- Create: `mai/src/mai/git/__init__.py`
- Create: `mai/src/mai/git/types.py`
- Create: `mai/src/mai/git/client.py` (protocol + `GitError` only this task)
- Create: `mai/src/mai/git/fake.py`
- Create: `mai/tests/test_git_fake.py`

**Interfaces:**
- Produces: `CommitFileMeta(path, change_type, old_path, added, removed)`;
  `CommitMeta(sha, author, authored_at, committer, committed_at, message, parents, is_merge, patch_id, files)`;
  `GitClient` protocol with `ensure_mirror(core, url)`, `fetch(core)`, `new_commits(core, since_sha) -> list[CommitMeta]`;
  `GitError`; `FakeGitClient(commits: dict[str, list[CommitMeta]])`.

- [ ] **Step 1: Create `git/__init__.py` (empty marker)**

```python
```

- [ ] **Step 2: Write `git/types.py`**

```python
from dataclasses import dataclass, field


@dataclass
class CommitFileMeta:
    path: str
    change_type: str            # A | M | D | R | C | T
    old_path: str | None = None
    added: int = 0
    removed: int = 0


@dataclass
class CommitMeta:
    sha: str
    author: str
    authored_at: str            # ISO-8601 string
    committer: str
    committed_at: str
    message: str                # full commit body
    parents: list[str] = field(default_factory=list)
    is_merge: bool = False
    patch_id: str | None = None
    files: list[CommitFileMeta] = field(default_factory=list)
```

- [ ] **Step 3: Write `git/client.py` (protocol + error only for now)**

```python
from typing import Protocol

from mai.git.types import CommitMeta


class GitError(RuntimeError):
    """A git subprocess returned non-zero."""


class GitClient(Protocol):
    async def ensure_mirror(self, core: str, url: str) -> None: ...
    async def fetch(self, core: str) -> None: ...
    async def new_commits(self, core: str, since_sha: str | None) -> list[CommitMeta]: ...
```

- [ ] **Step 4: Write the failing test**

`mai/tests/test_git_fake.py`:

```python
from mai.git.fake import FakeGitClient
from mai.git.types import CommitMeta


def _c(sha: str) -> CommitMeta:
    return CommitMeta(sha=sha, author="a", authored_at="2026-01-01T00:00:00Z",
                      committer="a", committed_at="2026-01-01T00:00:00Z",
                      message=sha, parents=[], is_merge=False, patch_id=f"p-{sha}")


async def test_fake_new_commits_returns_all_when_since_none():
    client = FakeGitClient({"zero": [_c("s1"), _c("s2")]})
    await client.ensure_mirror("zero", "file:///x")  # no-op
    await client.fetch("zero")                        # no-op
    metas = await client.new_commits("zero", None)
    assert [m.sha for m in metas] == ["s1", "s2"]


async def test_fake_new_commits_filters_after_since_sha():
    client = FakeGitClient({"zero": [_c("s1"), _c("s2"), _c("s3")]})
    metas = await client.new_commits("zero", "s1")
    assert [m.sha for m in metas] == ["s2", "s3"]


async def test_fake_new_commits_returns_all_when_since_unknown():
    client = FakeGitClient({"zero": [_c("s1"), _c("s2")]})
    metas = await client.new_commits("zero", "deadbeef")
    assert [m.sha for m in metas] == ["s1", "s2"]
```

- [ ] **Step 5: Run test to verify it fails**

Run: `cd mai && pytest tests/test_git_fake.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mai.git.fake'`

- [ ] **Step 6: Write `git/fake.py`**

```python
from mai.git.types import CommitMeta


class FakeGitClient:
    """In-memory GitClient for tests. `commits` maps core -> oldest-first CommitMeta list."""

    def __init__(self, commits: dict[str, list[CommitMeta]] | None = None):
        self._commits = commits or {}

    async def ensure_mirror(self, core: str, url: str) -> None:
        return None

    async def fetch(self, core: str) -> None:
        return None

    async def new_commits(self, core: str, since_sha: str | None) -> list[CommitMeta]:
        items = self._commits.get(core, [])
        if since_sha is None:
            return list(items)
        shas = [c.sha for c in items]
        if since_sha in shas:
            return list(items[shas.index(since_sha) + 1:])
        return list(items)  # unknown cursor (e.g. force-push) -> re-walk all
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd mai && pytest tests/test_git_fake.py -v`
Expected: PASS (3 passed)

- [ ] **Step 8: Commit**

```bash
git add mai/src/mai/git/__init__.py mai/src/mai/git/types.py mai/src/mai/git/client.py mai/src/mai/git/fake.py mai/tests/test_git_fake.py
git commit -m "feat: GitClient protocol, CommitMeta types, FakeGitClient"
```

---

### Task 2: `Commit/CommitFile/CommitPatch` models + `CommitRepository`

**Files:**
- Modify: `mai/src/mai/db/models.py` (append three classes)
- Create: `mai/src/mai/repository/commits.py`
- Create: `mai/tests/test_commit_repository.py`

**Interfaces:**
- Consumes: `CommitMeta`, `CommitFileMeta` (Task 1); `subsystem_of` (`mai.drift.compare`).
- Produces: ORM `Commit`, `CommitFile`, `CommitPatch`;
  `CommitRepository(session)` with `exists(core, sha) -> bool` and `add_commit(core, meta) -> bool` (True if inserted).

- [ ] **Step 1: Append the three models to `db/models.py`**

Append at the end of `mai/src/mai/db/models.py` (all named imports — `String/Text/Integer/Boolean/JSON/ForeignKey/UniqueConstraint/Mapped/mapped_column/_uuid/_now/datetime` — already exist there):

```python
class Commit(Base):
    """Raw, append-only code truth: one git commit on a fork's default branch."""
    __tablename__ = "commit"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    core: Mapped[str] = mapped_column(String(64))
    sha: Mapped[str] = mapped_column(String(40))
    author: Mapped[str] = mapped_column(String(255))
    authored_at: Mapped[str] = mapped_column(String(40))
    committer: Mapped[str] = mapped_column(String(255))
    committed_at: Mapped[str] = mapped_column(String(40))
    message: Mapped[str] = mapped_column(Text)
    parent_shas: Mapped[list] = mapped_column(JSON, default=list)
    is_merge: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=_now)

    __table_args__ = (
        UniqueConstraint("core", "sha", name="uq_commit_identity"),
    )


class CommitFile(Base):
    """Raw per-file change within a commit (diffstat + rename + subsystem)."""
    __tablename__ = "commit_file"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    commit_id: Mapped[str] = mapped_column(ForeignKey("commit.id"))
    path: Mapped[str] = mapped_column(Text)
    change_type: Mapped[str] = mapped_column(String(4))   # A | M | D | R | C | T
    old_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    added_lines: Mapped[int] = mapped_column(Integer, default=0)
    removed_lines: Mapped[int] = mapped_column(Integer, default=0)
    subsystem: Mapped[str] = mapped_column(String(255))


class CommitPatch(Base):
    """Raw patch identity for a (non-merge) commit. patch_id is git's; the rest is reserved."""
    __tablename__ = "commit_patch"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    commit_id: Mapped[str] = mapped_column(ForeignKey("commit.id"), unique=True)
    patch_id: Mapped[str | None] = mapped_column(String(64), nullable=True)   # git patch-id --stable
    normalized_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)  # Phase-2 fallback
    aggregate_of: Mapped[str | None] = mapped_column(String(255), nullable=True)    # Phase-2 PR-aggregate
```

- [ ] **Step 2: Write the failing test**

`mai/tests/test_commit_repository.py`:

```python
from sqlalchemy import func, select

from mai.db.models import Commit, CommitFile, CommitPatch
from mai.git.types import CommitFileMeta, CommitMeta
from mai.repository.commits import CommitRepository


def _meta(sha: str, *, is_merge: bool = False) -> CommitMeta:
    files = [] if is_merge else [
        CommitFileMeta(path="src/game/Object/Player.cpp", change_type="M",
                       added=3, removed=1),
        CommitFileMeta(path="src/new.cpp", change_type="R",
                       old_path="src/old.cpp", added=0, removed=0),
    ]
    return CommitMeta(sha=sha, author="dev", authored_at="2026-01-01T00:00:00Z",
                      committer="dev", committed_at="2026-01-01T00:00:00Z",
                      message=f"fix {sha}", parents=["p1", "p2"] if is_merge else ["p1"],
                      is_merge=is_merge, patch_id=None if is_merge else f"pid-{sha}",
                      files=files)


async def test_add_commit_inserts_commit_files_and_patch(session):
    repo = CommitRepository(session)
    assert await repo.add_commit("three", _meta("abc")) is True
    await session.commit()
    assert await session.scalar(select(func.count()).select_from(Commit)) == 1
    assert await session.scalar(select(func.count()).select_from(CommitFile)) == 2
    assert await session.scalar(select(func.count()).select_from(CommitPatch)) == 1
    cf = await session.scalar(select(CommitFile).where(CommitFile.change_type == "R"))
    assert cf.old_path == "src/old.cpp"
    assert cf.subsystem == "src/game/Object" or cf.subsystem == "src"  # depth-3 of src/new.cpp -> "src"
    cp = await session.scalar(select(CommitPatch))
    assert cp.patch_id == "pid-abc"


async def test_add_commit_is_idempotent(session):
    repo = CommitRepository(session)
    await repo.add_commit("three", _meta("abc"))
    await session.commit()
    assert await repo.add_commit("three", _meta("abc")) is False
    await session.commit()
    assert await session.scalar(select(func.count()).select_from(Commit)) == 1


async def test_add_merge_commit_has_null_patch_and_no_files(session):
    repo = CommitRepository(session)
    await repo.add_commit("three", _meta("merge1", is_merge=True))
    await session.commit()
    assert await session.scalar(select(func.count()).select_from(CommitFile)) == 0
    cp = await session.scalar(select(CommitPatch))
    assert cp.patch_id is None
    c = await session.scalar(select(Commit))
    assert c.is_merge is True and c.parent_shas == ["p1", "p2"]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd mai && pytest tests/test_commit_repository.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mai.repository.commits'`

- [ ] **Step 4: Write `repository/commits.py`**

```python
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mai.db.models import Commit, CommitFile, CommitPatch
from mai.drift.compare import subsystem_of
from mai.git.types import CommitMeta


class CommitRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def exists(self, core: str, sha: str) -> bool:
        found = await self._session.scalar(
            select(Commit.id).where(Commit.core == core, Commit.sha == sha)
        )
        return found is not None

    async def add_commit(self, core: str, meta: CommitMeta) -> bool:
        """Insert a commit + its files + patch row. Returns False if already present."""
        if await self.exists(core, meta.sha):
            return False
        commit = Commit(
            core=core, sha=meta.sha, author=meta.author, authored_at=meta.authored_at,
            committer=meta.committer, committed_at=meta.committed_at,
            message=meta.message, parent_shas=list(meta.parents), is_merge=meta.is_merge,
        )
        self._session.add(commit)
        await self._session.flush()  # populate commit.id
        self._session.add(CommitPatch(commit_id=commit.id, patch_id=meta.patch_id))
        for f in meta.files:
            self._session.add(CommitFile(
                commit_id=commit.id, path=f.path, change_type=f.change_type,
                old_path=f.old_path, added_lines=f.added, removed_lines=f.removed,
                subsystem=subsystem_of(f.path),
            ))
        return True
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd mai && pytest tests/test_commit_repository.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add mai/src/mai/db/models.py mai/src/mai/repository/commits.py mai/tests/test_commit_repository.py
git commit -m "feat: Commit/CommitFile/CommitPatch models + CommitRepository (idempotent insert)"
```

---

### Task 3: `commits_harvest_repo` orchestration

**Files:**
- Create: `mai/src/mai/git_harvest.py`
- Create: `mai/tests/test_git_harvest.py`

**Interfaces:**
- Consumes: `GitClient` (Task 1), `CommitRepository` (Task 2), `CursorRepository` (existing), `Repo` (existing).
- Produces: `commits_harvest_repo(session, client, repo, *, max_commits=None) -> int` (number of new commits ingested).

- [ ] **Step 1: Write the failing test**

`mai/tests/test_git_harvest.py`:

```python
from sqlalchemy import func, select

from mai.db.models import Commit, Repo
from mai.git.fake import FakeGitClient
from mai.git.types import CommitFileMeta, CommitMeta
from mai.git_harvest import commits_harvest_repo
from mai.repository.cursors import CursorRepository

REPO = Repo(full_name="r-log/server", core="three",
            url="file:///dev/null")


def _c(sha: str, *, is_merge: bool = False) -> CommitMeta:
    files = [] if is_merge else [CommitFileMeta(path="src/a.cpp", change_type="M",
                                                added=1, removed=0)]
    return CommitMeta(sha=sha, author="d", authored_at="2026-01-01T00:00:00Z",
                      committer="d", committed_at="2026-01-01T00:00:00Z",
                      message=sha, parents=["x", "y"] if is_merge else ["x"],
                      is_merge=is_merge, patch_id=None if is_merge else f"p-{sha}",
                      files=files)


async def test_harvest_ingests_and_advances_cursor(session):
    session.add(REPO)
    await session.flush()
    client = FakeGitClient({"three": [_c("s1"), _c("s2")]})
    n = await commits_harvest_repo(session, client, REPO)
    await session.commit()
    assert n == 2
    assert await session.scalar(select(func.count()).select_from(Commit)) == 2
    assert await CursorRepository(session).get("r-log/server", "git_commit") == "s2"


async def test_harvest_idempotent_second_run(session):
    session.add(REPO)
    await session.flush()
    client = FakeGitClient({"three": [_c("s1"), _c("s2")]})
    await commits_harvest_repo(session, client, REPO)
    await session.commit()
    n2 = await commits_harvest_repo(session, client, REPO)
    await session.commit()
    assert n2 == 0
    assert await session.scalar(select(func.count()).select_from(Commit)) == 2


async def test_harvest_incremental_after_new_commit(session):
    session.add(REPO)
    await session.flush()
    commits = [_c("s1"), _c("s2")]
    client = FakeGitClient({"three": commits})
    await commits_harvest_repo(session, client, REPO)
    await session.commit()
    commits.append(_c("s3"))
    n = await commits_harvest_repo(session, client, REPO)
    await session.commit()
    assert n == 1
    assert await CursorRepository(session).get("r-log/server", "git_commit") == "s3"


async def test_harvest_max_commits_batches(session):
    session.add(REPO)
    await session.flush()
    client = FakeGitClient({"three": [_c("s1"), _c("s2"), _c("s3")]})
    n1 = await commits_harvest_repo(session, client, REPO, max_commits=2)
    await session.commit()
    assert n1 == 2
    assert await CursorRepository(session).get("r-log/server", "git_commit") == "s2"
    n2 = await commits_harvest_repo(session, client, REPO, max_commits=2)
    await session.commit()
    assert n2 == 1
    assert await CursorRepository(session).get("r-log/server", "git_commit") == "s3"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mai && pytest tests/test_git_harvest.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mai.git_harvest'`

- [ ] **Step 3: Write `git_harvest.py`**

```python
from sqlalchemy.ext.asyncio import AsyncSession

from mai.db.models import Repo
from mai.git.client import GitClient
from mai.repository.commits import CommitRepository
from mai.repository.cursors import CursorRepository

_CURSOR_SOURCE = "git_commit"


async def commits_harvest_repo(session: AsyncSession, client: GitClient, repo: Repo,
                               *, max_commits: int | None = None) -> int:
    """Ensure mirror, fetch, ingest new commits since the SHA cursor, advance the cursor.

    Commits are processed oldest-first; the cursor advances to the newest ingested SHA,
    so a `max_commits` batch resumes cleanly on the next run.
    """
    cursors = CursorRepository(session)
    commits = CommitRepository(session)

    await client.ensure_mirror(repo.core, repo.url)
    await client.fetch(repo.core)

    since = await cursors.get(repo.full_name, _CURSOR_SOURCE)
    metas = await client.new_commits(repo.core, since)
    if max_commits is not None:
        metas = metas[:max_commits]

    count = 0
    last_sha = since
    for meta in metas:
        if await commits.add_commit(repo.core, meta):
            count += 1
        last_sha = meta.sha

    if last_sha is not None and last_sha != since:
        await cursors.set(repo.full_name, _CURSOR_SOURCE, last_sha)
    return count
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd mai && pytest tests/test_git_harvest.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add mai/src/mai/git_harvest.py mai/tests/test_git_harvest.py
git commit -m "feat: commits_harvest_repo orchestration (SHA cursor, idempotent, batchable)"
```

---

### Task 4: `LocalGitClient` (real git subprocess) + hermetic integration test

**Files:**
- Modify: `mai/src/mai/git/client.py` (append `LocalGitClient`)
- Create: `mai/tests/test_local_git_client.py`

**Interfaces:**
- Consumes: `CommitMeta`, `CommitFileMeta`, `GitError` (Task 1).
- Produces: `LocalGitClient(mirror_dir)` implementing the `GitClient` protocol over bare `--mirror` clones.

- [ ] **Step 1: Append `LocalGitClient` to `git/client.py`**

Add the imports at the top of `mai/src/mai/git/client.py` (keep the existing `Protocol`/`GitError`/`GitClient`), then append the class:

```python
import asyncio
from pathlib import Path

from mai.git.types import CommitFileMeta, CommitMeta


class LocalGitClient:
    """Production GitClient: async subprocess over bare `--mirror` clones under mirror_dir."""

    def __init__(self, mirror_dir: str):
        self._root = Path(mirror_dir)

    def _path(self, core: str) -> Path:
        return self._root / f"{core}.git"

    async def _run(self, args: list[str], *, cwd: str | None = None,
                   stdin: bytes | None = None) -> str:
        proc = await asyncio.create_subprocess_exec(
            "git", *args, cwd=cwd,
            stdin=asyncio.subprocess.PIPE if stdin is not None else None,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate(input=stdin)
        if proc.returncode != 0:
            raise GitError(f"git {' '.join(args)} -> {proc.returncode}: "
                           f"{err.decode('utf-8', 'replace').strip()}")
        return out.decode("utf-8", "replace")

    async def _git(self, core: str, *args: str, stdin: bytes | None = None) -> str:
        return await self._run(["-C", str(self._path(core)), *args], stdin=stdin)

    async def ensure_mirror(self, core: str, url: str) -> None:
        path = self._path(core)
        if (path / "HEAD").exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        await self._run(["clone", "--mirror", url, str(path)])

    async def fetch(self, core: str) -> None:
        await self._git(core, "fetch", "--prune")

    async def new_commits(self, core: str, since_sha: str | None) -> list[CommitMeta]:
        rng = "HEAD" if since_sha is None else f"{since_sha}..HEAD"
        out = await self._git(core, "rev-list", "--reverse", rng)
        shas = [s for s in out.splitlines() if s]
        return [await self._commit_meta(core, s) for s in shas]

    async def _commit_meta(self, core: str, sha: str) -> CommitMeta:
        # structured header fields (no body — body fetched separately to avoid newline clashes)
        fmt = "%H%n%an%n%aI%n%cn%n%cI%n%P"
        head = (await self._git(core, "show", "-s", f"--format={fmt}", sha)).split("\n")
        full_sha, an, aiso, cn, ciso, parents_line = head[0], head[1], head[2], head[3], head[4], head[5]
        parents = parents_line.split() if parents_line.strip() else []
        is_merge = len(parents) > 1
        message = (await self._git(core, "show", "-s", "--format=%B", sha)).rstrip("\n")
        patch_id = None
        files: list[CommitFileMeta] = []
        if not is_merge:
            patch_id = await self._patch_id(core, sha)
            files = await self._files(core, sha)
        return CommitMeta(sha=full_sha, author=an, authored_at=aiso, committer=cn,
                          committed_at=ciso, message=message, parents=parents,
                          is_merge=is_merge, patch_id=patch_id, files=files)

    async def _patch_id(self, core: str, sha: str) -> str | None:
        patch = await self._git(core, "diff-tree", "--root", "-p", "-M", sha)
        if not patch.strip():
            return None
        out = await self._git(core, "patch-id", "--stable",
                              stdin=patch.encode("utf-8", "replace"))
        parts = out.split()
        return parts[0] if parts else None

    async def _files(self, core: str, sha: str) -> list[CommitFileMeta]:
        # name-status and numstat are emitted in the SAME file order by the same diff walk,
        # so we zip them by index (robust against paths with spaces).
        names = (await self._git(core, "show", "-M", "--name-status", "--format=", sha)).strip("\n")
        nums = (await self._git(core, "show", "-M", "--numstat", "--format=", sha)).strip("\n")
        name_rows = [r for r in names.split("\n") if r]
        num_rows = [r for r in nums.split("\n") if r]
        files: list[CommitFileMeta] = []
        for name_line, num_line in zip(name_rows, num_rows):
            nparts = name_line.split("\t")
            change = nparts[0][0]                       # A | M | D | R | C | T
            if change in ("R", "C") and len(nparts) >= 3:
                old_path, path = nparts[1], nparts[2]
            else:
                old_path, path = None, nparts[-1]
            cols = num_line.split("\t")
            added = int(cols[0]) if cols[0].isdigit() else 0   # "-" for binary
            removed = int(cols[1]) if cols[1].isdigit() else 0
            files.append(CommitFileMeta(path=path, change_type=change,
                                        old_path=old_path, added=added, removed=removed))
        return files
```

- [ ] **Step 2: Write the integration test (real git, hermetic temp repo)**

`mai/tests/test_local_git_client.py`:

```python
import shutil
import subprocess

import pytest

from mai.git.client import LocalGitClient

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not on PATH")


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _make_source_repo(path):
    path.mkdir()
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@t.t")
    _git(path, "config", "user.name", "T")
    _git(path, "config", "commit.gpgsign", "false")
    (path / "a.txt").write_text("one\n")
    _git(path, "add", "a.txt")
    _git(path, "commit", "-q", "-m", "add a")
    (path / "a.txt").write_text("one\ntwo\n")
    _git(path, "add", "a.txt")
    _git(path, "commit", "-q", "-m", "grow a")
    _git(path, "mv", "a.txt", "b.txt")
    _git(path, "commit", "-q", "-m", "rename a to b")


async def test_local_client_harvests_real_commits(tmp_path):
    src = tmp_path / "src"
    _make_source_repo(src)
    client = LocalGitClient(str(tmp_path / "mirrors"))

    await client.ensure_mirror("test", src.as_uri())
    await client.fetch("test")
    metas = await client.new_commits("test", None)

    assert [m.message for m in metas] == ["add a", "grow a", "rename a to b"]
    assert all(m.patch_id for m in metas)           # every non-merge commit has a patch-id
    # rename detected on the third commit
    rename_files = metas[2].files
    assert any(f.change_type == "R" and f.old_path == "a.txt" and f.path == "b.txt"
               for f in rename_files)
    # second commit added exactly one line
    assert metas[1].files[0].added == 1


async def test_local_client_patch_id_is_deterministic(tmp_path):
    src = tmp_path / "src"
    _make_source_repo(src)
    client = LocalGitClient(str(tmp_path / "mirrors"))
    await client.ensure_mirror("test", src.as_uri())
    first = await client.new_commits("test", None)
    second = await client.new_commits("test", None)   # re-walk
    assert [m.patch_id for m in first] == [m.patch_id for m in second]


async def test_local_client_incremental_since_sha(tmp_path):
    src = tmp_path / "src"
    _make_source_repo(src)
    client = LocalGitClient(str(tmp_path / "mirrors"))
    await client.ensure_mirror("test", src.as_uri())
    metas = await client.new_commits("test", None)
    tail = await client.new_commits("test", metas[0].sha)
    assert [m.message for m in tail] == ["grow a", "rename a to b"]
```

- [ ] **Step 3: Run the integration test**

Run: `cd mai && pytest tests/test_local_git_client.py -v`
Expected: PASS (3 passed). If `git` is not on PATH, all 3 are SKIPPED — note that in the result.

- [ ] **Step 4: Commit**

```bash
git add mai/src/mai/git/client.py mai/tests/test_local_git_client.py
git commit -m "feat: LocalGitClient (mirror clone, patch-id, numstat+rename) with hermetic git test"
```

---

### Task 5: config + CLI `commits-harvest`, full-suite green, offline smoke

**Files:**
- Modify: `mai/src/mai/config.py` (add `git_mirror_dir`)
- Modify: `mai/src/mai/cli/__main__.py` (add subcommand)

**Interfaces:**
- Consumes: `LocalGitClient` (Task 4), `commits_harvest_repo` (Task 3), `RepoRepository` (existing), `settings` (existing).

- [ ] **Step 1: Add `git_mirror_dir` to `config.py`**

In `mai/src/mai/config.py`, add one field to `Settings` (below `drift_subsystem_depth`):

```python
    git_mirror_dir: str = "./mirrors"
```

- [ ] **Step 2: Add the `_commits_harvest` coroutine to `cli/__main__.py`**

Add after the existing `_drift` coroutine in `mai/src/mai/cli/__main__.py`:

```python
async def _commits_harvest() -> int:
    from mai.git.client import LocalGitClient
    from mai.git_harvest import commits_harvest_repo

    client = LocalGitClient(settings.git_mirror_dir)
    async with SessionFactory() as session:
        repos = await RepoRepository(session).all()
        total = 0
        for repo in repos:
            total += await commits_harvest_repo(session, client, repo)
            await session.commit()
    return total
```

- [ ] **Step 3: Register + dispatch the subcommand in `main()`**

In `main()`, add the parser registration after `sub.add_parser("drift")`:

```python
    sub.add_parser("commits-harvest")
```

And add the dispatch branch after the `drift` branch:

```python
    elif args.cmd == "commits-harvest":
        count = asyncio.run(_commits_harvest())
        print(f"commits-harvest: {count} new commits")
```

- [ ] **Step 4: Run the full test suite**

Run: `cd mai && pytest -q`
Expected: PASS — the existing suite (108) plus the new tests from this plan (3 + 3 + 4 + 3 = 13), so 121 passed (the 3 `LocalGitClient` tests skip if `git` is absent → 118 passed, 3 skipped).

- [ ] **Step 5: Offline smoke against a local throwaway repo**

```bash
cd mai && rm -rf /tmp/mai_src /tmp/mai_mirrors && mkdir -p /tmp/mai_src && \
  git -C /tmp/mai_src init -q && git -C /tmp/mai_src config user.email t@t.t && \
  git -C /tmp/mai_src config user.name T && echo hi > /tmp/mai_src/x.txt && \
  git -C /tmp/mai_src add x.txt && git -C /tmp/mai_src commit -q -m "seed" && \
  python -c "import asyncio; from mai.git.client import LocalGitClient; from pathlib import Path; \
c=LocalGitClient('/tmp/mai_mirrors'); \
print(asyncio.run((lambda: (c.ensure_mirror('t', Path('/tmp/mai_src').as_uri())))())) " 2>/dev/null || true
```

Then verify via a tiny script (avoid `python -c` with SQL — see memory): write `mai-data/tmp/smoke_commits.py`:

```python
import asyncio
from pathlib import Path
from mai.git.client import LocalGitClient

async def main():
    c = LocalGitClient("/tmp/mai_mirrors")
    await c.ensure_mirror("t", Path("/tmp/mai_src").as_uri())
    await c.fetch("t")
    metas = await c.new_commits("t", None)
    print("commits:", [m.message.strip() for m in metas])
    print("patch_ids:", [m.patch_id for m in metas])

asyncio.run(main())
```

Run: `cd mai && python mai-data/tmp/smoke_commits.py`
Expected: prints `commits: ['seed']` and a non-null patch id. (This exercises the real git path end-to-end without network.)

- [ ] **Step 6: Commit**

```bash
git add mai/src/mai/config.py mai/src/mai/cli/__main__.py
git commit -m "feat: mai CLI commits-harvest subcommand + git_mirror_dir setting"
```

---

## Self-Review

- **Spec coverage (Phase 1 slice of `sync-intelligence-engine.md` §13):** `GitClient` protocol + `LocalGitClient` (bare `--mirror` clone, `fetch`, `new_commits`, `patch_id`) + `FakeGitClient` ✓ (Tasks 1, 4); `Commit/CommitFile/CommitPatch` tables + repo ✓ (Task 2); commit SHA cursor via reused `SyncCursor` ✓ (Task 3); CLI `commits-harvest` ✓ (Task 5). Captures patch-id, line magnitude, and renames — §6.1 fields all populated. **Out of scope by design (Phase 2+):** PatchGroup/Propagation matching, cherry/aggregate detection, SubsystemClass, SyncObservation, PortCandidate, triggers/App. The `normalized_hash`/`aggregate_of` columns are created but left null (labeled Phase-2 fallback), matching Invariant 7.
- **Invariants:** read-only (git read only) ✓ · raw append-only (`Commit*` immutable, idempotent insert) ✓ · git-vouches (`patch-id --stable`, no hand-rolled hash) ✓ · commit-anchored cursor (SHA, not timestamp) ✓ · install-target-agnostic (repos from `Repo` registry) ✓.
- **Placeholder scan:** none — every step has runnable code/commands and expected output.
- **Type consistency:** `GitClient.new_commits(core, since_sha) -> list[CommitMeta]` matches `FakeGitClient` and `LocalGitClient`; `CommitRepository.add_commit(core, meta) -> bool` consumes `CommitMeta`; `commits_harvest_repo(session, client, repo, *, max_commits=None) -> int`; cursor `source_type="git_commit"` is identical in `git_harvest.py` and the tests; `subsystem_of` import path `mai.drift.compare` matches the existing module.

## Notes for later plans (Phase 2+)

- **Phase 2 (`14-...`):** `PatchGroup`/`Propagation` from `CommitPatch.patch_id` across cores; `git cherry`/`--cherry-mark` + `(cherry picked from commit …)` trailer parsing (uses the full `Commit.message` body captured here); PR-aggregate patch-id (`base...head`) into `CommitPatch.aggregate_of` for squash-merge bridging; `SubsystemClass`; `PortCandidate` with confidence+evidence; the §12 validation gates (golden cherry-pick on r-log forks, design-divergence anchor, squash fixture).
- **Force-push handling:** `new_commits` uses `since..HEAD`; if a fork rebases history the cursor SHA may vanish — Phase 4 should re-walk from merge-base. `FakeGitClient` already models the "unknown cursor → re-walk all" fallback.
- **First-clone cost:** `ensure_mirror` does a full `--mirror` clone; for the four real mangos repos consider shallow-then-deepen (spec §14 risk #2).
- **Migrations:** new tables still rely on `Base.metadata.create_all`; the Postgres/Neon deploy plan introduces Alembic + a baseline.
