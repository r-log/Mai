# Mai GitHub Harvester Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the GitHub harvester — fetch issues and pull requests from each tracked repo, normalize them into the existing `IntakeEvent` contract, ingest them idempotently, and advance a per-repo/per-source incremental cursor so re-runs only pull deltas.

**Architecture:** A `GitHubClient` protocol abstracts the API so orchestration is testable without network: `FakeGitHubClient` (canned dicts) for unit tests, `HttpGitHubClient` (httpx) for production. `normalize_issue`/`normalize_pull` map GitHub JSON to `IntakeEvent`; issues that are actually PRs (the `pull_request` key) are filtered out so they only come from the pulls endpoint. `harvest_repo` orchestrates: read cursor → list → ingest stream → advance cursor. All DB access stays behind the repository seam.

**Tech Stack:** Python 3.12 · httpx (new dep) · SQLAlchemy 2.0 async · pytest + pytest-asyncio · `httpx.MockTransport` for client tests.

---

## Builds on Plan 01

These already exist and MUST be reused as-is (do not redefine):
- `mai.contracts.IntakeEvent(source_type, source_id, title, core, status="open", repo_full_name=None, raw_payload={})` with `.canonical_key()` → `f"{source_type}:{source_id}"`.
- `mai.ingest.ingest_event(session, evt)` — idempotent append-only raw + derived report + event.
- `mai.db.models` — `Repo(full_name, core, url)`, `SourceRecord`, `Report`, plus helpers `_uuid`, `_now`.
- `mai.repository.repos.RepoRepository` (has `.all()`).
- `tests/conftest.py` — the async in-memory sqlite `session` fixture.

Invariants from Plan 01 still hold: immutable IDs, append-only raw, temporal cursors, one ingestion contract, repository seam.

## File Structure

```
src/mai/
  config.py                        # MODIFY: add github_token, github_api_url
  db/models.py                     # MODIFY: add SyncCursor
  github/
    __init__.py                    # new (empty)
    client.py                      # GitHubClient protocol + HttpGitHubClient
    fake.py                        # FakeGitHubClient (tests)
    normalize.py                   # normalize_issue / normalize_pull
  harvest.py                       # harvest_repo orchestration
  repository/cursors.py            # CursorRepository (seam)
  cli/__main__.py                  # MODIFY: add registry-load, harvest
tests/
  test_cursors.py
  test_github_normalize.py
  test_harvest.py
  test_github_client.py
```

---

### Task 1: SyncCursor model + CursorRepository

**Files:**
- Modify: `mai/src/mai/db/models.py` (append a class)
- Create: `mai/src/mai/repository/cursors.py`
- Create: `mai/tests/test_cursors.py`

- [ ] **Step 1: Add the `SyncCursor` model to `db/models.py`**

Append at the end of `mai/src/mai/db/models.py` (the imports `String`, `UniqueConstraint`, `Mapped`, `mapped_column`, `datetime`, `_now`, `_uuid` already exist in that file):

```python
class SyncCursor(Base):
    """Per-repo, per-source incremental fetch cursor (temporal)."""
    __tablename__ = "sync_cursor"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    repo_full_name: Mapped[str] = mapped_column(String(255))
    source_type: Mapped[str] = mapped_column(String(32))
    last_updated_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)

    __table_args__ = (
        UniqueConstraint("repo_full_name", "source_type", name="uq_sync_cursor"),
    )
```

- [ ] **Step 2: Write the failing test**

`mai/tests/test_cursors.py`:

```python
from sqlalchemy import func, select

from mai.db.models import SyncCursor
from mai.repository.cursors import CursorRepository


async def test_cursor_get_returns_none_when_unset(session):
    assert await CursorRepository(session).get("mangoszero/server", "gh_issue") is None


async def test_cursor_set_then_get_roundtrips(session):
    cur = CursorRepository(session)
    await cur.set("mangoszero/server", "gh_issue", "2026-01-01T00:00:00Z")
    await session.commit()
    assert await cur.get("mangoszero/server", "gh_issue") == "2026-01-01T00:00:00Z"


async def test_cursor_set_updates_existing_single_row(session):
    cur = CursorRepository(session)
    await cur.set("mangoszero/server", "gh_issue", "2026-01-01T00:00:00Z")
    await cur.set("mangoszero/server", "gh_issue", "2026-02-01T00:00:00Z")
    await session.commit()
    assert await cur.get("mangoszero/server", "gh_issue") == "2026-02-01T00:00:00Z"
    assert await session.scalar(select(func.count()).select_from(SyncCursor)) == 1
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd mai && pytest tests/test_cursors.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mai.repository.cursors'`

- [ ] **Step 4: Write `repository/cursors.py`**

```python
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mai.db.models import SyncCursor


class CursorRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get(self, repo_full_name: str, source_type: str) -> str | None:
        return await self._session.scalar(
            select(SyncCursor.last_updated_at).where(
                SyncCursor.repo_full_name == repo_full_name,
                SyncCursor.source_type == source_type,
            )
        )

    async def set(self, repo_full_name: str, source_type: str, value: str) -> None:
        existing = await self._session.scalar(
            select(SyncCursor).where(
                SyncCursor.repo_full_name == repo_full_name,
                SyncCursor.source_type == source_type,
            )
        )
        if existing:
            existing.last_updated_at = value
        else:
            self._session.add(SyncCursor(
                repo_full_name=repo_full_name,
                source_type=source_type,
                last_updated_at=value,
            ))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd mai && pytest tests/test_cursors.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add mai/src/mai/db/models.py mai/src/mai/repository/cursors.py mai/tests/test_cursors.py
git commit -m "feat: SyncCursor model + CursorRepository for incremental harvest"
```

---

### Task 2: Normalize GitHub JSON to IntakeEvent

**Files:**
- Create: `mai/src/mai/github/__init__.py`
- Create: `mai/src/mai/github/normalize.py`
- Create: `mai/tests/test_github_normalize.py`

- [ ] **Step 1: Write the failing test**

`mai/tests/test_github_normalize.py`:

```python
from mai.contracts import IntakeEvent
from mai.github.normalize import normalize_issue, normalize_pull

ISSUE = {"number": 5, "title": "Crash on login", "state": "open",
         "updated_at": "2026-03-01T00:00:00Z", "body": "boom"}
PR_IN_ISSUES = {"number": 9, "title": "Fix crash", "state": "closed",
                "updated_at": "2026-03-02T00:00:00Z", "pull_request": {"url": "x"}}
PR_MERGED = {"number": 12, "title": "Fix threat", "state": "closed",
             "merged_at": "2026-03-03T00:00:00Z", "updated_at": "2026-03-03T00:00:00Z"}
PR_OPEN = {"number": 13, "title": "WIP", "state": "open",
           "merged_at": None, "updated_at": "2026-03-04T00:00:00Z"}


def test_normalize_issue_maps_fields():
    evt = normalize_issue("mangoszero/server", "zero", ISSUE)
    assert isinstance(evt, IntakeEvent)
    assert evt.source_type == "gh_issue"
    assert evt.source_id == "mangoszero/server#5"
    assert evt.canonical_key() == "gh_issue:mangoszero/server#5"
    assert evt.core == "zero"
    assert evt.status == "open"
    assert evt.repo_full_name == "mangoszero/server"
    assert evt.raw_payload == ISSUE


def test_normalize_issue_returns_none_for_pull_request():
    assert normalize_issue("mangoszero/server", "zero", PR_IN_ISSUES) is None


def test_normalize_pull_merged_status():
    evt = normalize_pull("mangoszero/server", "zero", PR_MERGED)
    assert evt.source_type == "gh_pr"
    assert evt.source_id == "mangoszero/server#12"
    assert evt.canonical_key() == "gh_pr:mangoszero/server#12"
    assert evt.status == "merged"


def test_normalize_pull_open_status():
    assert normalize_pull("mangoszero/server", "zero", PR_OPEN).status == "open"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mai && pytest tests/test_github_normalize.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mai.github'`

- [ ] **Step 3: Create `github/__init__.py` (empty marker)**

```python
```

- [ ] **Step 4: Write `github/normalize.py`**

```python
from mai.contracts import IntakeEvent


def normalize_issue(repo_full_name: str, core: str, item: dict) -> IntakeEvent | None:
    """Map a GitHub issue to an IntakeEvent. Returns None if the item is a PR."""
    if "pull_request" in item:
        return None
    return IntakeEvent(
        source_type="gh_issue",
        source_id=f"{repo_full_name}#{item['number']}",
        title=item["title"],
        core=core,
        status=item["state"],
        repo_full_name=repo_full_name,
        raw_payload=item,
    )


def normalize_pull(repo_full_name: str, core: str, item: dict) -> IntakeEvent:
    """Map a GitHub pull request to an IntakeEvent."""
    status = "merged" if item.get("merged_at") else item["state"]
    return IntakeEvent(
        source_type="gh_pr",
        source_id=f"{repo_full_name}#{item['number']}",
        title=item["title"],
        core=core,
        status=status,
        repo_full_name=repo_full_name,
        raw_payload=item,
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd mai && pytest tests/test_github_normalize.py -v`
Expected: PASS (4 passed)

- [ ] **Step 6: Commit**

```bash
git add mai/src/mai/github/__init__.py mai/src/mai/github/normalize.py mai/tests/test_github_normalize.py
git commit -m "feat: normalize GitHub issues/PRs to IntakeEvent (PRs filtered from issues)"
```

---

### Task 3: Harvest orchestration + FakeGitHubClient

**Files:**
- Create: `mai/src/mai/github/client.py` (protocol only in this task)
- Create: `mai/src/mai/github/fake.py`
- Create: `mai/src/mai/harvest.py`
- Create: `mai/tests/test_harvest.py`

- [ ] **Step 1: Write the `GitHubClient` protocol in `github/client.py`**

(The `HttpGitHubClient` implementation is added in Task 4; this task only needs the protocol + fake.)

```python
from typing import Protocol


class GitHubClient(Protocol):
    async def list_issues(self, repo: str, since: str | None = None) -> list[dict]: ...
    async def list_pulls(self, repo: str, since: str | None = None) -> list[dict]: ...
```

- [ ] **Step 2: Write `github/fake.py`**

```python
class FakeGitHubClient:
    """In-memory GitHubClient for tests. Honors `since` by filtering on updated_at."""

    def __init__(self, issues: dict[str, list[dict]] | None = None,
                 pulls: dict[str, list[dict]] | None = None):
        self._issues = issues or {}
        self._pulls = pulls or {}

    async def list_issues(self, repo: str, since: str | None = None) -> list[dict]:
        return self._filter(self._issues.get(repo, []), since)

    async def list_pulls(self, repo: str, since: str | None = None) -> list[dict]:
        return self._filter(self._pulls.get(repo, []), since)

    @staticmethod
    def _filter(items: list[dict], since: str | None) -> list[dict]:
        if since is None:
            return list(items)
        return [i for i in items if i["updated_at"] > since]
```

- [ ] **Step 3: Write the failing test**

`mai/tests/test_harvest.py`:

```python
from sqlalchemy import func, select

from mai.db.models import Report, SourceRecord, Repo
from mai.github.fake import FakeGitHubClient
from mai.harvest import harvest_repo
from mai.repository.cursors import CursorRepository

REPO_NAME = "mangoszero/server"
ISSUES = [
    {"number": 1, "title": "Bug A", "state": "open", "updated_at": "2026-01-01T00:00:00Z"},
    {"number": 2, "title": "PR-in-issues", "state": "closed",
     "updated_at": "2026-01-02T00:00:00Z", "pull_request": {"url": "x"}},
]
PULLS = [
    {"number": 10, "title": "Fix A", "state": "closed",
     "merged_at": "2026-01-03T00:00:00Z", "updated_at": "2026-01-03T00:00:00Z"},
]


def _repo() -> Repo:
    return Repo(full_name=REPO_NAME, core="zero", url=f"https://github.com/{REPO_NAME}")


async def test_harvest_ingests_issues_and_pulls_skipping_pr_in_issues(session):
    session.add(_repo())
    await session.flush()
    client = FakeGitHubClient(issues={REPO_NAME: list(ISSUES)}, pulls={REPO_NAME: list(PULLS)})
    await harvest_repo(session, client, _repo())
    await session.commit()
    assert await session.scalar(select(func.count()).select_from(SourceRecord)) == 2
    assert await session.scalar(select(func.count()).select_from(Report)) == 2
    keys = set(await session.scalars(select(Report.canonical_key)))
    assert keys == {"gh_issue:mangoszero/server#1", "gh_pr:mangoszero/server#10"}


async def test_harvest_advances_cursor_to_newest_seen(session):
    session.add(_repo())
    await session.flush()
    client = FakeGitHubClient(issues={REPO_NAME: list(ISSUES)}, pulls={REPO_NAME: list(PULLS)})
    await harvest_repo(session, client, _repo())
    await session.commit()
    cur = CursorRepository(session)
    # newest issue-stream item seen is the skipped PR-in-issues (#2, 2026-01-02)
    assert await cur.get(REPO_NAME, "gh_issue") == "2026-01-02T00:00:00Z"
    assert await cur.get(REPO_NAME, "gh_pr") == "2026-01-03T00:00:00Z"


async def test_harvest_is_incremental_on_second_run(session):
    session.add(_repo())
    await session.flush()
    issues = list(ISSUES)
    client = FakeGitHubClient(issues={REPO_NAME: issues}, pulls={REPO_NAME: list(PULLS)})
    await harvest_repo(session, client, _repo())
    await session.commit()
    before = await session.scalar(select(func.count()).select_from(SourceRecord))
    issues.append({"number": 3, "title": "Bug C", "state": "open",
                   "updated_at": "2026-05-01T00:00:00Z"})
    await harvest_repo(session, client, _repo())
    await session.commit()
    after = await session.scalar(select(func.count()).select_from(SourceRecord))
    assert after == before + 1  # only the newer issue #3 was fetched + ingested
```

- [ ] **Step 4: Run test to verify it fails**

Run: `cd mai && pytest tests/test_harvest.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mai.harvest'`

- [ ] **Step 5: Write `harvest.py`**

```python
from sqlalchemy.ext.asyncio import AsyncSession

from mai.db.models import Repo
from mai.github.client import GitHubClient
from mai.github.normalize import normalize_issue, normalize_pull
from mai.ingest import ingest_event
from mai.repository.cursors import CursorRepository


async def _ingest_stream(session: AsyncSession, items: list[dict], repo: Repo,
                         normalize_fn) -> str | None:
    """Ingest each normalizable item; return the newest updated_at SEEN (incl. skipped)."""
    newest = None
    for item in items:
        ts = item.get("updated_at")
        if ts is not None and (newest is None or ts > newest):
            newest = ts
        evt = normalize_fn(repo.full_name, repo.core, item)
        if evt is not None:
            await ingest_event(session, evt)
    return newest


async def harvest_repo(session: AsyncSession, client: GitHubClient, repo: Repo) -> None:
    cursors = CursorRepository(session)

    since_i = await cursors.get(repo.full_name, "gh_issue")
    issues = await client.list_issues(repo.full_name, since_i)
    newest_i = await _ingest_stream(session, issues, repo, normalize_issue)
    if newest_i is not None:
        await cursors.set(repo.full_name, "gh_issue", newest_i)

    since_p = await cursors.get(repo.full_name, "gh_pr")
    pulls = await client.list_pulls(repo.full_name, since_p)
    newest_p = await _ingest_stream(session, pulls, repo, normalize_pull)
    if newest_p is not None:
        await cursors.set(repo.full_name, "gh_pr", newest_p)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd mai && pytest tests/test_harvest.py -v`
Expected: PASS (3 passed)

- [ ] **Step 7: Commit**

```bash
git add mai/src/mai/github/client.py mai/src/mai/github/fake.py mai/src/mai/harvest.py mai/tests/test_harvest.py
git commit -m "feat: harvest_repo orchestration with incremental cursors + FakeGitHubClient"
```

---

### Task 4: HttpGitHubClient (real httpx client)

**Files:**
- Modify: `mai/pyproject.toml` (add httpx dep)
- Modify: `mai/src/mai/github/client.py` (add `HttpGitHubClient`)
- Create: `mai/tests/test_github_client.py`

- [ ] **Step 1: Add `httpx` to dependencies in `pyproject.toml`**

In `mai/pyproject.toml`, add `"httpx>=0.27"` to the `dependencies` list so it reads:

```toml
dependencies = [
    "sqlalchemy>=2.0",
    "pydantic-settings>=2.0",
    "aiosqlite>=0.19",
    "asyncpg>=0.29",
    "httpx>=0.27",
]
```

- [ ] **Step 2: Install the new dep**

Run: `cd mai && pip install -e ".[dev]"`
Expected: installs httpx (or reports "already satisfied").

- [ ] **Step 3: Write the failing test**

`mai/tests/test_github_client.py`:

```python
import httpx

from mai.github.client import HttpGitHubClient


def _handler(request: httpx.Request) -> httpx.Response:
    assert request.headers["Authorization"] == "Bearer t0ken"
    if request.url.path == "/repos/mangoszero/server/issues":
        return httpx.Response(200, json=[
            {"number": 1, "title": "A", "state": "open", "updated_at": "2026-01-01T00:00:00Z"},
            {"number": 2, "title": "B", "state": "closed",
             "updated_at": "2026-01-02T00:00:00Z", "pull_request": {"url": "x"}},
        ])
    if request.url.path == "/repos/mangoszero/server/pulls":
        return httpx.Response(200, json=[
            {"number": 10, "title": "P", "state": "open", "merged_at": None,
             "updated_at": "2026-02-01T00:00:00Z"},
        ])
    return httpx.Response(404, json={})


async def test_http_client_lists_issues_with_auth_header():
    transport = httpx.MockTransport(_handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = HttpGitHubClient("t0ken", client=http)
        issues = await client.list_issues("mangoszero/server")
    assert [i["number"] for i in issues] == [1, 2]  # raw incl PR; normalize filters later


async def test_http_client_filters_pulls_by_since():
    transport = httpx.MockTransport(_handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = HttpGitHubClient("t0ken", client=http)
        pulls = await client.list_pulls("mangoszero/server", since="2026-03-01T00:00:00Z")
    assert pulls == []  # the only PR (2026-02-01) is older than `since`
```

- [ ] **Step 4: Run test to verify it fails**

Run: `cd mai && pytest tests/test_github_client.py -v`
Expected: FAIL with `ImportError: cannot import name 'HttpGitHubClient'`

- [ ] **Step 5: Add `HttpGitHubClient` to `github/client.py`**

Append to `mai/src/mai/github/client.py` (keep the existing `GitHubClient` protocol; add the import line at top):

```python
import httpx

_PER_PAGE = 100


class HttpGitHubClient:
    """Production GitHubClient backed by httpx. Pass `client` to inject a transport."""

    def __init__(self, token: str, base_url: str = "https://api.github.com",
                 client: httpx.AsyncClient | None = None):
        self._base = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient()
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def list_issues(self, repo: str, since: str | None = None) -> list[dict]:
        params = {"state": "all", "sort": "updated", "direction": "asc"}
        if since is not None:
            params["since"] = since
        return await self._paginate(f"/repos/{repo}/issues", params)

    async def list_pulls(self, repo: str, since: str | None = None) -> list[dict]:
        params = {"state": "all", "sort": "updated", "direction": "asc"}
        items = await self._paginate(f"/repos/{repo}/pulls", params)
        if since is not None:
            items = [i for i in items if i["updated_at"] > since]
        return items

    async def _paginate(self, path: str, params: dict) -> list[dict]:
        results: list[dict] = []
        page = 1
        while True:
            resp = await self._client.get(
                self._base + path,
                params={**params, "per_page": _PER_PAGE, "page": page},
                headers=self._headers,
            )
            resp.raise_for_status()
            batch = resp.json()
            if not batch:
                break
            results.extend(batch)
            if len(batch) < _PER_PAGE:
                break
            page += 1
        return results
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd mai && pytest tests/test_github_client.py -v`
Expected: PASS (2 passed)

- [ ] **Step 7: Commit**

```bash
git add mai/pyproject.toml mai/src/mai/github/client.py mai/tests/test_github_client.py
git commit -m "feat: HttpGitHubClient with pagination + auth (MockTransport-tested)"
```

---

### Task 5: CLI wiring (registry-load + harvest) and full-suite green

**Files:**
- Modify: `mai/src/mai/config.py` (add github settings)
- Modify: `mai/src/mai/cli/__main__.py` (add subcommands)

- [ ] **Step 1: Add GitHub settings to `config.py`**

In `mai/src/mai/config.py`, add two fields to the `Settings` class (below `ledger_path`):

```python
    github_token: str | None = None
    github_api_url: str = "https://api.github.com"
```

- [ ] **Step 2: Add `registry-load` and `harvest` subcommands to `cli/__main__.py`**

Add these imports near the top of `mai/src/mai/cli/__main__.py`:

```python
from mai.repository.repos import RepoRepository
from mai.sources.registry import parse_registry
```

Add these two coroutine functions (after the existing `_publish`):

```python
async def _registry_load(readme_path: str) -> int:
    text = Path(readme_path).read_text(encoding="utf-8")
    async with SessionFactory() as session:
        repo_repo = RepoRepository(session)
        rows = parse_registry(text)
        for row in rows:
            await repo_repo.upsert(row.full_name, row.core, row.url)
        await session.commit()
    return len(rows)


async def _harvest() -> int:
    if not settings.github_token:
        raise SystemExit("GITHUB_TOKEN not set")
    import httpx

    from mai.github.client import HttpGitHubClient
    from mai.harvest import harvest_repo

    async with httpx.AsyncClient() as http:
        client = HttpGitHubClient(settings.github_token,
                                  base_url=settings.github_api_url, client=http)
        async with SessionFactory() as session:
            repos = await RepoRepository(session).all()
            for repo in repos:
                await harvest_repo(session, client, repo)
            await session.commit()
    return len(repos)
```

Then extend `main()` — register the parsers and dispatch. Replace the existing subparser/dispatch block so it reads:

```python
def main() -> None:
    parser = argparse.ArgumentParser(prog="mai")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db")
    sub.add_parser("publish")
    rl = sub.add_parser("registry-load")
    rl.add_argument("readme_path")
    sub.add_parser("harvest")
    args = parser.parse_args()

    if args.cmd == "init-db":
        asyncio.run(_init_db())
        print("db initialized")
    elif args.cmd == "publish":
        count = asyncio.run(_publish())
        print(f"published {count} reports")
    elif args.cmd == "registry-load":
        count = asyncio.run(_registry_load(args.readme_path))
        print(f"loaded {count} repos")
    elif args.cmd == "harvest":
        count = asyncio.run(_harvest())
        print(f"harvested {count} repos")
```

- [ ] **Step 3: Run the full test suite**

Run: `cd mai && pytest -q`
Expected: PASS (18 passed — 6 from Plan 01 + 12 new).

- [ ] **Step 4: Smoke-test registry-load offline against the test fixture**

Run:
```bash
cd mai && rm -f mai.db && python -m mai.cli.__main__ init-db && python -m mai.cli.__main__ registry-load tests/fixtures/mangos_readme.md
```
Expected: prints `db initialized` then `loaded 3 repos`.

- [ ] **Step 5: (Optional, needs a token) Smoke-test live harvest**

If a GitHub token is available, run:
```bash
cd mai && GITHUB_TOKEN=<token> python -m mai.cli.__main__ harvest
```
Expected: prints `harvested 3 repos` and populates `source_record`/`report` from the live API. If no token is available, SKIP this step and note it — the harvest logic is already covered by `test_harvest.py` with the fake client.

- [ ] **Step 6: Commit**

```bash
git add mai/src/mai/config.py mai/src/mai/cli/__main__.py
git commit -m "feat: mai CLI registry-load + harvest subcommands"
```

---

## Self-Review

- **Spec coverage:** Implements the GitHub side of spec §7 stage 2 (harvester: issues + PRs, incremental cursors) and §3's verified findings (PRs filtered out of the issues endpoint; PRs are first-class via `gh_pr`). Commits-as-breadcrumbs (§7) are intentionally deferred to a follow-up (`02b`) to keep this plan focused — noted here so it's not mistaken for complete coverage.
- **Invariants:** immutable IDs (`source_id = repo#number`) ✓ · append-only raw via reused `ingest_event` ✓ · temporal cursors (`SyncCursor`, advance-to-newest-seen) ✓ · one ingestion contract (`IntakeEvent`) ✓ · repository seam (`CursorRepository`, `RepoRepository`) ✓.
- **Placeholder scan:** none — every step has runnable code/commands.
- **Type consistency:** `GitHubClient.list_issues/list_pulls(repo, since)` signatures match both `FakeGitHubClient` and `HttpGitHubClient`; `normalize_issue` returns `IntakeEvent | None` and `harvest._ingest_stream` handles the `None`; `canonical_key()` form `gh_pr:owner/repo#n` is consistent across normalize tests and harvest assertions.

## Notes for later plans

- **Commits harvest (`02b`)**: add `list_commits` + `normalize_commit` (breadcrumb regex on message), same cursor pattern.
- **Migrations**: `SyncCursor` and all Plan 01 tables still rely on `Base.metadata.create_all`; the first Postgres deploy plan (06) introduces Alembic and a baseline migration.
- **Rate limiting**: `HttpGitHubClient` does not yet implement the central token-bucket / backoff from spec §10 — add before running across the full repo set at scale.
