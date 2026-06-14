# Mai Foundation (Walking Skeleton) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the end-to-end thin slice of Mai — parse the repo registry, ingest a normalized intake event into an immutable-raw + derived store, and project a report to a Markdown ledger file — proving the pipeline and the five invariants with zero external services.

**Architecture:** Python service mirroring GITA's shape (FastAPI/worker later; CLI now). One ingestion contract (`IntakeEvent`) feeds a normalize step that writes an append-only `source_record` and upserts a derived `report`; all DB access goes through a repository seam; reports project to `.md` with versioned front-matter. Tests use a real transactional SQLite session (Postgres in prod via the same SQLAlchemy models); no network.

**Tech Stack:** Python 3.12 · SQLAlchemy 2.0 (async) · pydantic-settings · pytest + pytest-asyncio · aiosqlite (tests) · alembic (migrations).

---

## Plan-wide invariants (from spec §4)

1. Key only on immutable IDs; `report` ↔ source via `report_source_map`.
2. `source_record` is append-only and immutable; `report` is derived/recomputable.
3. Temporal columns (`observed_at`/`fetched_at`) on stateful rows.
4. One ingestion contract (`IntakeEvent`); sources are adapters.
5. All DB access goes through `repository/`; no business logic touches raw SQL.

## File Structure

```
mai/
  pyproject.toml                     # package + deps + pytest config
  src/mai/
    __init__.py
    config.py                        # pydantic-settings
    db/
      __init__.py
      base.py                        # DeclarativeBase
      models.py                      # repos, source_record, report, report_source_map, event
      session.py                     # async engine + session factory
    contracts.py                     # IntakeEvent (the one ingestion contract)
    sources/
      __init__.py
      registry.py                    # parse mangos/MaNGOS README -> repo rows
    repository/
      __init__.py
      repos.py                       # RepoRepository (seam)
      reports.py                     # ReportRepository (seam: ingest + read)
    ingest.py                        # normalize IntakeEvent -> source_record + report + event
    publish/
      __init__.py
      markdown.py                    # report -> .md front-matter string
    cli/
      __init__.py
      __main__.py                    # `mai` CLI entry
  tests/
    conftest.py                      # async db session fixture
    fixtures/mangos_readme.md        # sample README for the registry parser
    test_registry.py
    test_ingest.py
    test_markdown.py
```

---

### Task 1: Project scaffold

**Files:**
- Create: `mai/pyproject.toml`
- Create: `mai/src/mai/__init__.py`
- Create: `mai/src/mai/config.py`

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "mai"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "sqlalchemy>=2.0",
    "pydantic-settings>=2.0",
    "aiosqlite>=0.19",
    "asyncpg>=0.29",
]

[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-asyncio>=0.23"]

[project.scripts]
mai = "mai.cli.__main__:main"

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
pythonpath = ["src"]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"
```

- [ ] **Step 2: Create the package marker**

`mai/src/mai/__init__.py`:

```python
__all__ = ["__version__"]
__version__ = "0.1.0"
```

- [ ] **Step 3: Write `config.py`**

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./mai.db"
    ledger_path: str = "./mai-data"


settings = Settings()
```

- [ ] **Step 4: Install and verify import**

Run: `cd mai && pip install -e ".[dev]" && python -c "import mai; print(mai.__version__)"`
Expected: prints `0.1.0`

- [ ] **Step 5: Commit**

```bash
git add mai/pyproject.toml mai/src/mai/__init__.py mai/src/mai/config.py
git commit -m "chore: scaffold mai package"
```

---

### Task 2: Database models and session

**Files:**
- Create: `mai/src/mai/db/__init__.py`
- Create: `mai/src/mai/db/base.py`
- Create: `mai/src/mai/db/models.py`
- Create: `mai/src/mai/db/session.py`

- [ ] **Step 1: Create `db/__init__.py` (empty marker)**

```python
```

- [ ] **Step 2: Write `db/base.py`**

```python
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
```

- [ ] **Step 3: Write `db/models.py`**

```python
import uuid
from datetime import datetime, timezone

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from mai.db.base import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Repo(Base):
    __tablename__ = "repos"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    full_name: Mapped[str] = mapped_column(String(255), unique=True)
    core: Mapped[str] = mapped_column(String(64))
    url: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=_now)


class SourceRecord(Base):
    """Immutable, append-only verbatim copy of a fetched artifact."""
    __tablename__ = "source_record"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    source_type: Mapped[str] = mapped_column(String(32))   # ips | gh_issue | gh_pr | gh_commit
    source_id: Mapped[str] = mapped_column(String(255))    # immutable id, e.g. r1842
    repo_full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, default=1)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    fetched_at: Mapped[datetime] = mapped_column(default=_now)

    __table_args__ = (
        UniqueConstraint("source_type", "source_id", "content_hash",
                         name="uq_source_record_identity"),
    )


class Report(Base):
    """Derived, recomputable canonical bug/finding."""
    __tablename__ = "report"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    canonical_key: Mapped[str] = mapped_column(String(255), unique=True)
    core: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="open")
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)


class ReportSourceMap(Base):
    __tablename__ = "report_source_map"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    report_id: Mapped[str] = mapped_column(ForeignKey("report.id"))
    source_type: Mapped[str] = mapped_column(String(32))
    source_id: Mapped[str] = mapped_column(String(255))

    __table_args__ = (
        UniqueConstraint("source_type", "source_id", name="uq_rsm_source"),
    )


class Event(Base):
    """Immutable temporal change log."""
    __tablename__ = "event"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    report_id: Mapped[str | None] = mapped_column(ForeignKey("report.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(32))   # ingested | status_changed | retracted
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    observed_at: Mapped[datetime] = mapped_column(default=_now)
```

- [ ] **Step 4: Write `db/session.py`**

```python
from sqlalchemy.ext.asyncio import (
    AsyncSession, async_sessionmaker, create_async_engine,
)

from mai.config import settings

engine = create_async_engine(settings.database_url, future=True)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False)
```

- [ ] **Step 5: Commit**

```bash
git add mai/src/mai/db/
git commit -m "feat: add db models and async session"
```

---

### Task 3: Test fixtures (async DB session)

**Files:**
- Create: `mai/tests/conftest.py`

- [ ] **Step 1: Write `conftest.py`**

```python
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from mai.db.base import Base


@pytest_asyncio.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()
```

- [ ] **Step 2: Verify the fixture loads (no tests yet)**

Run: `cd mai && pytest -q`
Expected: `no tests ran` (collection succeeds, zero tests)

- [ ] **Step 3: Commit**

```bash
git add mai/tests/conftest.py
git commit -m "test: add async in-memory db session fixture"
```

---

### Task 4: Repository seam — RepoRepository

**Files:**
- Create: `mai/src/mai/repository/__init__.py`
- Create: `mai/src/mai/repository/repos.py`
- Test: `mai/tests/test_registry.py` (created in Task 5; this task is covered by Task 5's tests)

- [ ] **Step 1: Create `repository/__init__.py` (empty marker)**

```python
```

- [ ] **Step 2: Write `repository/repos.py`**

```python
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mai.db.models import Repo


class RepoRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def upsert(self, full_name: str, core: str, url: str) -> Repo:
        existing = await self._session.scalar(
            select(Repo).where(Repo.full_name == full_name)
        )
        if existing:
            existing.core, existing.url = core, url
            return existing
        repo = Repo(full_name=full_name, core=core, url=url)
        self._session.add(repo)
        return repo

    async def all(self) -> list[Repo]:
        return list(await self._session.scalars(select(Repo).order_by(Repo.full_name)))
```

- [ ] **Step 3: Commit**

```bash
git add mai/src/mai/repository/
git commit -m "feat: add RepoRepository seam"
```

---

### Task 5: Registry adapter (parse MaNGOS README)

**Files:**
- Create: `mai/tests/fixtures/mangos_readme.md`
- Create: `mai/tests/test_registry.py`
- Create: `mai/src/mai/sources/__init__.py`
- Create: `mai/src/mai/sources/registry.py`

- [ ] **Step 1: Create the fixture README**

`mai/tests/fixtures/mangos_readme.md`:

```markdown
# MaNGOS repositories

- [MangosZero](https://github.com/mangoszero/server)
- [MangosTwo](https://github.com/mangostwo/server)
- [MangosThree](https://github.com/mangosthree/server)
- Not a repo link: https://www.getmangos.eu/
- [Duplicate](https://github.com/mangoszero/server)
```

- [ ] **Step 2: Write the failing test**

`mai/tests/test_registry.py`:

```python
from pathlib import Path

from mai.sources.registry import parse_registry
from mai.repository.repos import RepoRepository

FIXTURE = Path(__file__).parent / "fixtures" / "mangos_readme.md"


def test_parse_registry_extracts_unique_github_repos():
    rows = parse_registry(FIXTURE.read_text())
    full_names = [r.full_name for r in rows]
    assert full_names == ["mangosthree/server", "mangostwo/server", "mangoszero/server"]
    assert {r.core for r in rows} == {"zero", "two", "three"}


async def test_registry_rows_upsert_idempotently(session):
    repo_repo = RepoRepository(session)
    for row in parse_registry(FIXTURE.read_text()):
        await repo_repo.upsert(row.full_name, row.core, row.url)
    await session.commit()
    for row in parse_registry(FIXTURE.read_text()):
        await repo_repo.upsert(row.full_name, row.core, row.url)
    await session.commit()
    assert len(await repo_repo.all()) == 3
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd mai && pytest tests/test_registry.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mai.sources.registry'`

- [ ] **Step 4: Create `sources/__init__.py` (empty marker)**

```python
```

- [ ] **Step 5: Write `sources/registry.py`**

```python
import re
from dataclasses import dataclass

_REPO_RE = re.compile(r"https://github\.com/([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+)")
_CORE_BY_ORG = {
    "mangoszero": "zero", "mangosone": "one", "mangostwo": "two",
    "mangosthree": "three", "mangosfour": "four",
}


@dataclass(frozen=True)
class RegistryRow:
    full_name: str
    core: str
    url: str


def parse_registry(readme_markdown: str) -> list[RegistryRow]:
    seen: dict[str, RegistryRow] = {}
    for org, repo in _REPO_RE.findall(readme_markdown):
        full_name = f"{org}/{repo}"
        if full_name in seen:
            continue
        seen[full_name] = RegistryRow(
            full_name=full_name,
            core=_CORE_BY_ORG.get(org.lower(), "other"),
            url=f"https://github.com/{full_name}",
        )
    return sorted(seen.values(), key=lambda r: r.full_name)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd mai && pytest tests/test_registry.py -v`
Expected: PASS (2 passed)

- [ ] **Step 7: Commit**

```bash
git add mai/tests/fixtures/mangos_readme.md mai/tests/test_registry.py mai/src/mai/sources/
git commit -m "feat: registry adapter parses MaNGOS README into repo rows"
```

---

### Task 6: Ingestion contract + normalize

**Files:**
- Create: `mai/src/mai/contracts.py`
- Create: `mai/src/mai/repository/reports.py`
- Create: `mai/src/mai/ingest.py`
- Create: `mai/tests/test_ingest.py`

- [ ] **Step 1: Write `contracts.py`**

```python
from dataclasses import dataclass, field


@dataclass(frozen=True)
class IntakeEvent:
    """The single shape every source adapter emits (invariant 4)."""
    source_type: str          # ips | gh_issue | gh_pr | gh_commit
    source_id: str            # immutable id, e.g. "r1842"
    title: str
    core: str
    status: str = "open"
    repo_full_name: str | None = None
    raw_payload: dict = field(default_factory=dict)

    def canonical_key(self) -> str:
        return f"{self.source_type}:{self.source_id}"
```

- [ ] **Step 2: Write the failing test**

`mai/tests/test_ingest.py`:

```python
from sqlalchemy import func, select

from mai.contracts import IntakeEvent
from mai.db.models import Event, Report, ReportSourceMap, SourceRecord
from mai.ingest import ingest_event

EVT = IntakeEvent(
    source_type="ips", source_id="r1842",
    title="Agro from pet doesnt work", core="zero",
    status="open", raw_payload={"body": "threat union bug"},
)


async def test_ingest_creates_raw_report_map_and_event(session):
    await ingest_event(session, EVT)
    await session.commit()
    assert await session.scalar(select(func.count()).select_from(SourceRecord)) == 1
    assert await session.scalar(select(func.count()).select_from(Report)) == 1
    assert await session.scalar(select(func.count()).select_from(ReportSourceMap)) == 1
    assert await session.scalar(select(func.count()).select_from(Event)) == 1
    report = await session.scalar(select(Report))
    assert report.canonical_key == "ips:r1842"


async def test_ingest_is_idempotent_on_identical_payload(session):
    await ingest_event(session, EVT)
    await ingest_event(session, EVT)
    await session.commit()
    assert await session.scalar(select(func.count()).select_from(SourceRecord)) == 1
    assert await session.scalar(select(func.count()).select_from(Report)) == 1


async def test_ingest_appends_new_version_on_changed_payload(session):
    await ingest_event(session, EVT)
    changed = IntakeEvent(
        source_type="ips", source_id="r1842", title="Agro from pet doesnt work",
        core="zero", status="completed", raw_payload={"body": "EDITED"},
    )
    await ingest_event(session, changed)
    await session.commit()
    assert await session.scalar(select(func.count()).select_from(SourceRecord)) == 2
    assert await session.scalar(select(func.count()).select_from(Report)) == 1  # same report
    report = await session.scalar(select(Report))
    assert report.status == "completed"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd mai && pytest tests/test_ingest.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mai.ingest'`

- [ ] **Step 4: Write `repository/reports.py`**

```python
import hashlib
import json

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from mai.db.models import Event, Report, ReportSourceMap, SourceRecord


def content_hash(payload: dict) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


class ReportRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def latest_source_version(self, source_type: str, source_id: str) -> int:
        row = await self._session.scalar(
            select(SourceRecord.version)
            .where(SourceRecord.source_type == source_type,
                   SourceRecord.source_id == source_id)
            .order_by(desc(SourceRecord.version))
            .limit(1)
        )
        return row or 0

    async def source_exists(self, source_type: str, source_id: str, chash: str) -> bool:
        return bool(await self._session.scalar(
            select(SourceRecord.id).where(
                SourceRecord.source_type == source_type,
                SourceRecord.source_id == source_id,
                SourceRecord.content_hash == chash,
            )
        ))

    def add_source_record(self, **kw) -> SourceRecord:
        rec = SourceRecord(**kw)
        self._session.add(rec)
        return rec

    async def get_report(self, canonical_key: str) -> Report | None:
        return await self._session.scalar(
            select(Report).where(Report.canonical_key == canonical_key)
        )

    def add_report(self, **kw) -> Report:
        rep = Report(**kw)
        self._session.add(rep)
        return rep

    async def map_exists(self, source_type: str, source_id: str) -> bool:
        return bool(await self._session.scalar(
            select(ReportSourceMap.id).where(
                ReportSourceMap.source_type == source_type,
                ReportSourceMap.source_id == source_id,
            )
        ))

    def add_map(self, **kw) -> None:
        self._session.add(ReportSourceMap(**kw))

    def add_event(self, **kw) -> None:
        self._session.add(Event(**kw))
```

- [ ] **Step 5: Write `ingest.py`**

```python
from sqlalchemy.ext.asyncio import AsyncSession

from mai.contracts import IntakeEvent
from mai.repository.reports import ReportRepository, content_hash


async def ingest_event(session: AsyncSession, evt: IntakeEvent) -> None:
    """Normalize one IntakeEvent: append immutable raw, upsert derived report."""
    repo = ReportRepository(session)
    chash = content_hash(evt.raw_payload)

    if await repo.source_exists(evt.source_type, evt.source_id, chash):
        return  # idempotent: identical payload already stored

    next_version = await repo.latest_source_version(evt.source_type, evt.source_id) + 1
    repo.add_source_record(
        source_type=evt.source_type, source_id=evt.source_id,
        repo_full_name=evt.repo_full_name, content_hash=chash,
        version=next_version, payload=evt.raw_payload,
    )

    key = evt.canonical_key()
    report = await repo.get_report(key)
    if report is None:
        report = repo.add_report(
            canonical_key=key, core=evt.core, title=evt.title, status=evt.status,
        )
        await session.flush()
        repo.add_event(report_id=report.id, kind="ingested",
                       payload={"source_id": evt.source_id})
    else:
        if report.status != evt.status:
            repo.add_event(report_id=report.id, kind="status_changed",
                           payload={"from": report.status, "to": evt.status})
            report.status = evt.status

    if not await repo.map_exists(evt.source_type, evt.source_id):
        repo.add_map(report_id=report.id, source_type=evt.source_type,
                     source_id=evt.source_id)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd mai && pytest tests/test_ingest.py -v`
Expected: PASS (3 passed)

- [ ] **Step 7: Commit**

```bash
git add mai/src/mai/contracts.py mai/src/mai/repository/reports.py mai/src/mai/ingest.py mai/tests/test_ingest.py
git commit -m "feat: ingestion contract + idempotent normalize into raw/report/event"
```

---

### Task 7: Markdown ledger projection

**Files:**
- Create: `mai/src/mai/publish/__init__.py`
- Create: `mai/src/mai/publish/markdown.py`
- Create: `mai/tests/test_markdown.py`

- [ ] **Step 1: Write the failing test**

`mai/tests/test_markdown.py`:

```python
from mai.db.models import Report
from mai.publish.markdown import report_to_markdown

SCHEMA_VERSION = 1


def test_report_to_markdown_emits_versioned_frontmatter():
    report = Report(
        id="11111111-1111-1111-1111-111111111111",
        canonical_key="ips:r1842", core="zero",
        title="Agro from pet doesnt work", status="open",
    )
    md = report_to_markdown(report, sources=["ips:r1842"])
    assert md.startswith("---\n")
    assert f"schema_version: {SCHEMA_VERSION}" in md
    assert 'id: ips:r1842' in md
    assert "core: zero" in md
    assert "status: open" in md
    assert "sources:\n  - ips:r1842" in md
    assert md.rstrip().endswith("# Agro from pet doesnt work")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mai && pytest tests/test_markdown.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mai.publish.markdown'`

- [ ] **Step 3: Create `publish/__init__.py` (empty marker)**

```python
```

- [ ] **Step 4: Write `publish/markdown.py`**

```python
from mai.db.models import Report

SCHEMA_VERSION = 1


def report_to_markdown(report: Report, sources: list[str]) -> str:
    """Project a report to a versioned-front-matter ledger file (spec §9)."""
    lines = ["---", f"schema_version: {SCHEMA_VERSION}",
             f"id: {report.canonical_key}", f"core: {report.core}",
             f"status: {report.status}", "sources:"]
    lines += [f"  - {s}" for s in sources]
    lines += ["---", "", f"# {report.title}", ""]
    return "\n".join(lines)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd mai && pytest tests/test_markdown.py -v`
Expected: PASS (1 passed)

- [ ] **Step 6: Commit**

```bash
git add mai/src/mai/publish/ mai/tests/test_markdown.py
git commit -m "feat: project report to versioned markdown ledger file"
```

---

### Task 8: CLI wiring + full-suite green

**Files:**
- Create: `mai/src/mai/cli/__init__.py`
- Create: `mai/src/mai/cli/__main__.py`

- [ ] **Step 1: Create `cli/__init__.py` (empty marker)**

```python
```

- [ ] **Step 2: Write `cli/__main__.py`**

```python
import argparse
import asyncio
from pathlib import Path

from mai.config import settings
from mai.contracts import IntakeEvent
from mai.db.base import Base
from mai.db.session import SessionFactory, engine
from mai.ingest import ingest_event
from mai.publish.markdown import report_to_markdown
from mai.repository.reports import ReportRepository
from mai.db.models import Report, ReportSourceMap
from sqlalchemy import select


async def _init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _publish() -> int:
    out = Path(settings.ledger_path) / "content"
    async with SessionFactory() as session:
        reports = list(await session.scalars(select(Report)))
        for report in reports:
            src = list(await session.scalars(
                select(ReportSourceMap).where(ReportSourceMap.report_id == report.id)
            ))
            keys = [f"{m.source_type}:{m.source_id}" for m in src]
            target = out / report.core / "bugs"
            target.mkdir(parents=True, exist_ok=True)
            (target / f"{report.canonical_key.replace(':', '-')}.md").write_text(
                report_to_markdown(report, keys), encoding="utf-8"
            )
    return len(reports)


def main() -> None:
    parser = argparse.ArgumentParser(prog="mai")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db")
    sub.add_parser("publish")
    args = parser.parse_args()

    if args.cmd == "init-db":
        asyncio.run(_init_db())
        print("db initialized")
    elif args.cmd == "publish":
        count = asyncio.run(_publish())
        print(f"published {count} reports")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run the full test suite**

Run: `cd mai && pytest -q`
Expected: PASS (6 passed)

- [ ] **Step 4: Smoke-test the CLI end to end**

Run:
```bash
cd mai && python -c "
import asyncio
from mai.db.base import Base
from mai.db.session import engine, SessionFactory
from mai.contracts import IntakeEvent
from mai.ingest import ingest_event

async def go():
    async with engine.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    async with SessionFactory() as s:
        await ingest_event(s, IntakeEvent('ips','r1842','Agro from pet','zero'))
        await s.commit()
asyncio.run(go())
" && python -m mai.cli.__main__ publish && cat mai-data/content/zero/bugs/ips-r1842.md
```
Expected: prints `published 1 reports` then the Markdown file with front-matter and `# Agro from pet`.

- [ ] **Step 5: Commit**

```bash
git add mai/src/mai/cli/
git commit -m "feat: mai CLI (init-db, publish) wiring the walking skeleton end to end"
```

---

## Self-Review

- **Spec coverage:** This plan implements the foundation for spec §5 (one service, repository seam), §6 (repos/source_record/report/report_source_map/event), §7 stages 1/2/4 in thin form (registry → ingest → publish), §9 (IntakeEvent contract + versioned `.md` front-matter). Stage 3 (enrich/correlation/drift), the GitHub/IPS real adapters, and all Cloudflare infra (§8) are explicitly deferred to plans 02–06.
- **Invariants:** 1 (canonical_key + report_source_map) ✓ · 2 (append-only SourceRecord, idempotent + version-on-change) ✓ · 3 (fetched_at/observed_at, Event log) ✓ · 4 (IntakeEvent) ✓ · 5 (repository/ seam, store is SQLite in tests / Postgres in prod via same models) ✓.
- **Placeholder scan:** none — every step has runnable code/commands.
- **Type consistency:** `IntakeEvent.canonical_key()` → `"ips:r1842"` matches `Report.canonical_key` and the `report_to_markdown` `id:` field and the CLI filename derivation; `ReportRepository` method names used consistently in `ingest.py`.

## Notes for later plans

- pgvector is **not** introduced here (no embeddings yet) so tests run on SQLite. Plan 04 adds it, at which point `JSON`→`JSONB` and a `Vector(1536)` column land via an Alembic migration, and correlation tests need a Postgres test DB.
- Alembic init is deferred to the first plan that needs a real Postgres migration (02/06); the skeleton uses `Base.metadata.create_all`.
