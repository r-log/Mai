# Mai Drift Observatory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure cross-core drift — for each pair of sibling forks, how many files in each subsystem are identical, diverged, or unique to one side — and store it as a queryable drift map. The second Mai lens ("how far apart are the forks, and where").

**Architecture:** Two forks' files with identical content share the same **git blob SHA**, so drift is computed by comparing each fork's recursive tree (`path → blob_sha`) from the GitHub **Trees API** — no git clone, no local checkout (so it stays serverless-friendly). A pure `compare_trees` rolls path-level identical/diverged/unique counts up by subsystem; `compute_drift` fetches trees per fork pair and stores `drift_obs` rows. A `TreeClient` protocol keeps it offline-testable (`FakeTreeClient` for tests, `GitHubTreeClient` for production).

**Tech Stack:** Python 3.12 · httpx · SQLAlchemy 2.0 async · pytest + pytest-asyncio · `httpx.MockTransport` · stdlib `itertools`.

---

## Builds on Plans 01–06

Reuse as-is (do NOT redefine):
- `mai.db.models` helpers (`_uuid`, `_now`); `mai.repository.repos.RepoRepository` (`.all()`).
- `tests/conftest.py` `session` fixture; config/CLI patterns.

**Design principles:** drift observations are **derived & recomputable** (computed from external trees, never mutate other tables); idempotent upserts; repository seam; offline-testable.

## File Structure

```
src/mai/
  config.py                 # MODIFY: drift_subsystem_depth
  db/models.py              # MODIFY: add DriftObservation
  drift/
    __init__.py             # new (empty)
    compare.py              # subsystem_of, compare_trees (pure)
    client.py               # TreeClient protocol + GitHubTreeClient
    fake.py                 # FakeTreeClient
    run.py                  # compute_drift, default_pairs
  repository/drift.py       # DriftRepository
  cli/__main__.py           # MODIFY: add drift subcommand
tests/
  test_drift_compare.py
  test_drift_repo.py
  test_drift_run.py
  test_tree_client.py
```

---

### Task 1: DriftObservation model + DriftRepository

**Files:**
- Modify: `mai/src/mai/db/models.py` (append `DriftObservation`)
- Create: `mai/src/mai/repository/drift.py`
- Create: `mai/tests/test_drift_repo.py`

- [ ] **Step 1: Append the `DriftObservation` model at the end of `db/models.py`**

(The imports `String`, `Integer`, `UniqueConstraint`, `datetime`, `_uuid`, `_now`, `Mapped`, `mapped_column` already exist.)

```python
class DriftObservation(Base):
    """Derived per-subsystem divergence between two forks (one row per pair+subsystem)."""
    __tablename__ = "drift_obs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    fork_a: Mapped[str] = mapped_column(String(255))
    fork_b: Mapped[str] = mapped_column(String(255))
    subsystem: Mapped[str] = mapped_column(String(255))
    shared: Mapped[int] = mapped_column(Integer, default=0)
    diverged: Mapped[int] = mapped_column(Integer, default=0)
    identical: Mapped[int] = mapped_column(Integer, default=0)
    only_a: Mapped[int] = mapped_column(Integer, default=0)
    only_b: Mapped[int] = mapped_column(Integer, default=0)
    observed_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)

    __table_args__ = (
        UniqueConstraint("fork_a", "fork_b", "subsystem", name="uq_drift_obs"),
    )
```

- [ ] **Step 2: Write the failing test**

`mai/tests/test_drift_repo.py`:

```python
from sqlalchemy import func, select

from mai.db.models import DriftObservation
from mai.repository.drift import DriftRepository

STATS = {"shared": 10, "diverged": 4, "identical": 6, "only_a": 1, "only_b": 2}


async def test_upsert_inserts_then_updates_single_row(session):
    repo = DriftRepository(session)
    await repo.upsert("zero/server", "two/server", "src/game/Object", STATS)
    await session.commit()
    await repo.upsert("zero/server", "two/server", "src/game/Object",
                      {**STATS, "diverged": 5})  # same key -> update, no dup
    await session.commit()
    rows = await repo.for_pair("zero/server", "two/server")
    assert len(rows) == 1
    assert rows[0].diverged == 5
    assert rows[0].shared == 10
    assert await session.scalar(select(func.count()).select_from(DriftObservation)) == 1


async def test_for_pair_returns_only_that_pair(session):
    repo = DriftRepository(session)
    await repo.upsert("zero/server", "two/server", "src/shared", STATS)
    await repo.upsert("one/server", "two/server", "src/shared", STATS)
    await session.commit()
    assert len(await repo.for_pair("zero/server", "two/server")) == 1
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd mai && pytest tests/test_drift_repo.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mai.repository.drift'`

- [ ] **Step 4: Write `repository/drift.py`**

```python
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mai.db.models import DriftObservation

_FIELDS = ("shared", "diverged", "identical", "only_a", "only_b")


class DriftRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def upsert(self, fork_a: str, fork_b: str, subsystem: str,
                     stats: dict) -> None:
        existing = await self._session.scalar(
            select(DriftObservation).where(
                DriftObservation.fork_a == fork_a,
                DriftObservation.fork_b == fork_b,
                DriftObservation.subsystem == subsystem,
            )
        )
        if existing:
            for field in _FIELDS:
                setattr(existing, field, stats[field])
        else:
            self._session.add(DriftObservation(
                fork_a=fork_a, fork_b=fork_b, subsystem=subsystem,
                **{field: stats[field] for field in _FIELDS}))

    async def for_pair(self, fork_a: str, fork_b: str) -> list[DriftObservation]:
        return list(await self._session.scalars(
            select(DriftObservation).where(
                DriftObservation.fork_a == fork_a,
                DriftObservation.fork_b == fork_b,
            )
        ))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd mai && pytest tests/test_drift_repo.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add mai/src/mai/db/models.py mai/src/mai/repository/drift.py mai/tests/test_drift_repo.py
git commit -m "feat: DriftObservation model + DriftRepository"
```

---

### Task 2: Tree comparison (pure functions)

**Files:**
- Create: `mai/src/mai/drift/__init__.py`
- Create: `mai/src/mai/drift/compare.py`
- Create: `mai/tests/test_drift_compare.py`

- [ ] **Step 1: Write the failing test**

`mai/tests/test_drift_compare.py`:

```python
from mai.drift.compare import compare_trees, subsystem_of


def test_subsystem_of_groups_by_directory_depth():
    assert subsystem_of("src/game/Object/Player.cpp", 3) == "src/game/Object"
    assert subsystem_of("src/game/Object/Item/Item.cpp", 3) == "src/game/Object"
    assert subsystem_of("src/shared/Log.cpp", 3) == "src/shared"
    assert subsystem_of("README.md", 3) == "(root)"


def test_compare_trees_counts_identical_diverged_and_unique():
    a = {
        "src/game/Object/Player.cpp": "sha_p1",
        "src/game/Object/Unit.cpp": "sha_u",
        "src/shared/Log.cpp": "sha_l",
        "OnlyA.txt": "sha_a",
    }
    b = {
        "src/game/Object/Player.cpp": "sha_p2",   # diverged
        "src/game/Object/Unit.cpp": "sha_u",      # identical
        "src/shared/Log.cpp": "sha_l",            # identical
        "OnlyB.txt": "sha_b",
    }
    stats = compare_trees(a, b, depth=3)
    obj = stats["src/game/Object"]
    assert (obj["shared"], obj["diverged"], obj["identical"]) == (2, 1, 1)
    shared = stats["src/shared"]
    assert (shared["shared"], shared["identical"]) == (1, 1)
    root = stats["(root)"]
    assert (root["only_a"], root["only_b"]) == (1, 1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mai && pytest tests/test_drift_compare.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mai.drift'`

- [ ] **Step 3: Create `drift/__init__.py` (empty marker)**

```python
```

- [ ] **Step 4: Write `drift/compare.py`**

```python
def subsystem_of(path: str, depth: int = 3) -> str:
    """Group a file path by its directory, truncated to `depth` segments."""
    parts = path.split("/")
    if len(parts) == 1:
        return "(root)"
    return "/".join(parts[:-1][:depth])


def _blank() -> dict:
    return {"shared": 0, "diverged": 0, "identical": 0, "only_a": 0, "only_b": 0}


def compare_trees(tree_a: dict[str, str], tree_b: dict[str, str],
                  depth: int = 3) -> dict[str, dict]:
    """Per-subsystem counts of identical/diverged/unique files between two trees.

    Trees map path -> git blob SHA; equal SHA means byte-identical content.
    """
    stats: dict[str, dict] = {}
    for path in set(tree_a) | set(tree_b):
        bucket = stats.setdefault(subsystem_of(path, depth), _blank())
        in_a, in_b = path in tree_a, path in tree_b
        if in_a and in_b:
            bucket["shared"] += 1
            if tree_a[path] == tree_b[path]:
                bucket["identical"] += 1
            else:
                bucket["diverged"] += 1
        elif in_a:
            bucket["only_a"] += 1
        else:
            bucket["only_b"] += 1
    return stats
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd mai && pytest tests/test_drift_compare.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add mai/src/mai/drift/__init__.py mai/src/mai/drift/compare.py mai/tests/test_drift_compare.py
git commit -m "feat: subsystem-grouped tree comparison (blob-SHA drift)"
```

---

### Task 3: TreeClient protocol, FakeTreeClient, and orchestration

**Files:**
- Create: `mai/src/mai/drift/client.py` (protocol only in this task)
- Create: `mai/src/mai/drift/fake.py`
- Create: `mai/src/mai/drift/run.py`
- Create: `mai/tests/test_drift_run.py`

- [ ] **Step 1: Write `drift/client.py` (protocol only; GitHubTreeClient added in Task 4)**

```python
from typing import Protocol


class TreeClient(Protocol):
    async def get_tree(self, repo: str) -> dict[str, str]: ...
```

- [ ] **Step 2: Write `drift/fake.py`**

```python
class FakeTreeClient:
    """In-memory TreeClient for tests: repo full_name -> {path: blob_sha}."""

    def __init__(self, trees: dict[str, dict[str, str]]):
        self._trees = trees

    async def get_tree(self, repo: str) -> dict[str, str]:
        return dict(self._trees.get(repo, {}))
```

- [ ] **Step 3: Write the failing test**

`mai/tests/test_drift_run.py`:

```python
from mai.drift.fake import FakeTreeClient
from mai.drift.run import compute_drift, default_pairs
from mai.repository.drift import DriftRepository
from mai.repository.repos import RepoRepository


async def test_compute_drift_stores_per_subsystem(session):
    client = FakeTreeClient({
        "mangoszero/server": {"src/game/Object/Player.cpp": "a", "common.txt": "c"},
        "mangostwo/server": {"src/game/Object/Player.cpp": "b", "common.txt": "c"},
    })
    n = await compute_drift(session, client,
                            [("mangoszero/server", "mangostwo/server")], depth=3)
    await session.commit()
    assert n >= 1
    rows = {r.subsystem: r for r in
            await DriftRepository(session).for_pair("mangoszero/server", "mangostwo/server")}
    assert rows["src/game/Object"].diverged == 1
    assert rows["(root)"].identical == 1


async def test_default_pairs_builds_pairs_of_server_repos(session):
    rr = RepoRepository(session)
    await rr.upsert("mangoszero/server", "zero", "u")
    await rr.upsert("mangostwo/server", "two", "u")
    await rr.upsert("mangoszero/database", "zero", "u")  # not a server repo
    await session.commit()
    pairs = await default_pairs(session)
    assert len(pairs) == 1
    assert set(pairs[0]) == {"mangoszero/server", "mangostwo/server"}
```

- [ ] **Step 4: Run test to verify it fails**

Run: `cd mai && pytest tests/test_drift_run.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mai.drift.run'`

- [ ] **Step 5: Write `drift/run.py`**

```python
from itertools import combinations

from sqlalchemy.ext.asyncio import AsyncSession

from mai.drift.client import TreeClient
from mai.drift.compare import compare_trees
from mai.repository.drift import DriftRepository
from mai.repository.repos import RepoRepository


async def compute_drift(session: AsyncSession, client: TreeClient,
                        pairs: list[tuple[str, str]], depth: int = 3) -> int:
    """For each fork pair, fetch trees, compare, and store per-subsystem drift."""
    drepo = DriftRepository(session)
    rows = 0
    for fork_a, fork_b in pairs:
        tree_a = await client.get_tree(fork_a)
        tree_b = await client.get_tree(fork_b)
        for subsystem, stats in compare_trees(tree_a, tree_b, depth).items():
            await drepo.upsert(fork_a, fork_b, subsystem, stats)
            rows += 1
        await session.commit()
    return rows


async def default_pairs(session: AsyncSession) -> list[tuple[str, str]]:
    """All unordered pairs of tracked `*/server` repos."""
    repos = await RepoRepository(session).all()
    servers = sorted(r.full_name for r in repos if r.full_name.endswith("/server"))
    return list(combinations(servers, 2))
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd mai && pytest tests/test_drift_run.py -v`
Expected: PASS (2 passed)

- [ ] **Step 7: Commit**

```bash
git add mai/src/mai/drift/client.py mai/src/mai/drift/fake.py mai/src/mai/drift/run.py mai/tests/test_drift_run.py
git commit -m "feat: drift orchestration (compute_drift + default_pairs) + FakeTreeClient"
```

---

### Task 4: GitHubTreeClient (real httpx client)

**Files:**
- Modify: `mai/src/mai/drift/client.py` (add `GitHubTreeClient`)
- Create: `mai/tests/test_tree_client.py`

- [ ] **Step 1: Write the failing test**

`mai/tests/test_tree_client.py`:

```python
import httpx

from mai.drift.client import GitHubTreeClient


def _handler(request: httpx.Request) -> httpx.Response:
    assert request.headers["Authorization"] == "Bearer t0ken"
    assert request.url.path == "/repos/mangoszero/server/git/trees/HEAD"
    assert request.url.params.get("recursive") == "1"
    return httpx.Response(200, json={
        "sha": "root",
        "tree": [
            {"path": "src/game/Object/Player.cpp", "type": "blob", "sha": "aaa"},
            {"path": "src/game/Object", "type": "tree", "sha": "bbb"},  # dir -> skipped
            {"path": "README.md", "type": "blob", "sha": "ccc"},
        ],
        "truncated": False,
    })


async def test_tree_client_returns_only_blob_paths():
    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as http:
        client = GitHubTreeClient("t0ken", client=http)
        tree = await client.get_tree("mangoszero/server")
    assert tree == {"src/game/Object/Player.cpp": "aaa", "README.md": "ccc"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mai && pytest tests/test_tree_client.py -v`
Expected: FAIL with `ImportError: cannot import name 'GitHubTreeClient'`

- [ ] **Step 3: Add `GitHubTreeClient` to `drift/client.py`**

Append to `mai/src/mai/drift/client.py` (keep the `TreeClient` protocol; add the import at top):

```python
import httpx


class GitHubTreeClient:
    """Production TreeClient backed by the GitHub Trees API (recursive, blob SHAs)."""

    def __init__(self, token: str, ref: str = "HEAD",
                 base_url: str = "https://api.github.com",
                 client: httpx.AsyncClient | None = None):
        self._ref = ref
        self._base = base_url.rstrip("/")
        # caller should inject a managed client (async with ...) in production
        self._client = client or httpx.AsyncClient()
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def get_tree(self, repo: str) -> dict[str, str]:
        resp = await self._client.get(
            f"{self._base}/repos/{repo}/git/trees/{self._ref}",
            params={"recursive": "1"},
            headers=self._headers,
        )
        resp.raise_for_status()
        data = resp.json()
        return {item["path"]: item["sha"]
                for item in data.get("tree", [])
                if item.get("type") == "blob"}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd mai && pytest tests/test_tree_client.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add mai/src/mai/drift/client.py mai/tests/test_tree_client.py
git commit -m "feat: GitHubTreeClient (recursive Trees API -> path/blob-sha map)"
```

---

### Task 5: CLI wiring (drift) and full-suite green

**Files:**
- Modify: `mai/src/mai/config.py` (add drift depth)
- Modify: `mai/src/mai/cli/__main__.py` (add drift subcommand)

- [ ] **Step 1: Add a setting to `config.py`**

In `mai/src/mai/config.py`, add one field to `Settings` (below the embedding fields):

```python
    drift_subsystem_depth: int = 3
```

- [ ] **Step 2: Add the `drift` subcommand to `cli/__main__.py`**

Add this coroutine (after `_correlate`):

```python
async def _drift() -> int:
    if not settings.github_token:
        raise SystemExit("GITHUB_TOKEN not set")
    import httpx

    from mai.drift.client import GitHubTreeClient
    from mai.drift.run import compute_drift, default_pairs

    async with httpx.AsyncClient(timeout=120.0) as http:
        client = GitHubTreeClient(settings.github_token,
                                  base_url=settings.github_api_url, client=http)
        async with SessionFactory() as session:
            pairs = await default_pairs(session)
            return await compute_drift(session, client, pairs,
                                       depth=settings.drift_subsystem_depth)
```

Register the parser (`sub.add_parser("drift")`) and add this dispatch branch:

```python
    elif args.cmd == "drift":
        rows = asyncio.run(_drift())
        print(f"drift: {rows} subsystem observations")
```

- [ ] **Step 3: Run the full test suite**

Run: `cd mai && pytest -q`
Expected: PASS (75 passed — 68 prior + 7 new).

- [ ] **Step 4: Verify the `drift` subcommand is wired (guard fires without a token)**

Run: `cd mai && GITHUB_TOKEN="" python -m mai.cli.__main__ drift` (PYTHONPATH=src if needed)
Expected: exits with `GITHUB_TOKEN not set`.

- [ ] **Step 5: (Optional, needs a token + loaded registry) Live drift smoke**

If `GITHUB_TOKEN` is set and `registry-load` has populated `*/server` repos:
```bash
cd mai && python -m mai.cli.__main__ drift
```
Expected: prints `drift: N subsystem observations`. If not set up, SKIP and note it — logic is covered by `test_drift_run.py` with the fake client.

- [ ] **Step 6: Commit**

```bash
git add mai/src/mai/config.py mai/src/mai/cli/__main__.py
git commit -m "feat: mai CLI drift subcommand"
```

---

## Self-Review

- **Spec coverage:** Implements spec §6 `drift_obs` and the Drift Observatory lens (spec §1, §5) — cross-core subsystem divergence, computed from GitHub blob SHAs (no local git, contradicting the earlier "needs real clones" assumption — recorded for the deploy plan).
- **Invariants:** derived & recomputable (drift computed from external trees; never mutates other tables) ✓ · idempotent upsert (`uq_drift_obs`) ✓ · temporal (`observed_at` onupdate) ✓ · pluggable client (Fake/GitHub behind `TreeClient`) ✓ · repository seam (`DriftRepository`) ✓ · offline-testable ✓.
- **Placeholder scan:** none — every step has runnable code/commands.
- **Type consistency:** `TreeClient.get_tree(repo) -> dict[str, str]` matches both clients; `compare_trees` output dict keys (`shared/diverged/identical/only_a/only_b`) match `DriftRepository._FIELDS` and the `DriftObservation` columns; `compute_drift` + `default_pairs` signatures match the CLI call.

## Notes for later plans

- **Truncated trees:** the GitHub Trees API truncates very large repos (`"truncated": true`). `get_tree` currently returns the partial tree silently; add a `truncated` flag/warning and a paginated fallback before trusting drift on the biggest repos.
- **Line-count / content deltas:** blob-SHA tells identical-vs-different, not *how* different. A later enhancement fetches blobs for diverged files to compute line-count deltas (like the workspace's WorldSession 1313-vs-1170 table).
- **Sync-commit tracking:** parse `[Sync] From ... MangosTwo` commit messages to show sync recency/direction per fork pair.
- **Publish (later):** render the drift matrix + per-core "behind on subsystem X" slices into the Hugo dashboard.
- **Serverless note:** because drift is pure GitHub API + comparison (no git binary), it can run in Cloudflare Workers/Cron — the deploy plan no longer needs a dedicated non-serverless worker solely for drift.
