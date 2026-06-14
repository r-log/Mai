# Mai IPS Bug-Tracker Crawler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the getmangos.eu (Invision/IPS) bug-tracker crawler — discover every bug detail URL, fetch each page via Firecrawl, parse its structured fields, normalize to the existing `IntakeEvent` contract keyed on the stable `rNNNN` id, and ingest idempotently with the full page preserved as raw.

**Architecture:** An `IpsClient` protocol abstracts Firecrawl so orchestration is testable without network: `FakeIpsClient` (canned pages) for unit tests, `FirecrawlIpsClient` (httpx → Firecrawl `/v1/map` + `/v1/scrape`) for production. `parse_bug_url` extracts `(core, rNNNN)` from a URL; `parse_bug_page` regex-extracts the labeled IPS fields (Status, Main/Sub-Category, Version, Milestone, Priority, Implemented Version) from the page markdown; `normalize_ips` builds the `IntakeEvent`, keeping the full markdown + parsed fields in `raw_payload` (invariant: raw is sacred). `crawl_all` discovers → fetches → ingests, committing per bug so a backfill is resumable.

**Tech Stack:** Python 3.12 · httpx · SQLAlchemy 2.0 async · pytest + pytest-asyncio · `httpx.MockTransport` for client tests · regex parsing.

---

## Builds on Plans 01–02

Reuse as-is (do NOT redefine):
- `mai.contracts.IntakeEvent(source_type, source_id, title, core, status="open", repo_full_name=None, raw_payload={})`, `.canonical_key()` → `f"{source_type}:{source_id}"`.
- `mai.ingest.ingest_event(session, evt)` — idempotent append-only raw + derived report + event.
- `mai.db.models.Report` / `SourceRecord`; `tests/conftest.py` `session` fixture.
- Config pattern in `mai/src/mai/config.py`; CLI pattern in `mai/src/mai/cli/__main__.py`.

Invariants hold: immutable IDs (`rNNNN` is the IPS-global stable key), append-only raw (full markdown stored), one ingestion contract, repository seam, replayable.

## File Structure

```
src/mai/
  config.py                 # MODIFY: firecrawl_api_key, firecrawl_api_url, ips_bug_tracker_url
  ips/
    __init__.py             # new (empty)
    parse.py                # parse_bug_url, parse_bug_page (IpsBug)
    normalize.py            # normalize_ips (+ SOURCE_IPS constant)
    client.py               # IpsClient protocol + FirecrawlIpsClient
    fake.py                 # FakeIpsClient (tests)
  ips_crawl.py              # crawl_all orchestration
  cli/__main__.py           # MODIFY: add ips-crawl subcommand
tests/
  fixtures/ips_bug_r1842.md
  test_ips_parse.py
  test_ips_normalize.py
  test_ips_crawl.py
  test_ips_client.py
```

---

### Task 1: Parse IPS URLs and detail pages

**Files:**
- Create: `mai/tests/fixtures/ips_bug_r1842.md`
- Create: `mai/tests/test_ips_parse.py`
- Create: `mai/src/mai/ips/__init__.py`
- Create: `mai/src/mai/ips/parse.py`

- [ ] **Step 1: Create the fixture page** (trimmed main-content of a real bug)

`mai/tests/fixtures/ips_bug_r1842.md`:

```markdown
# Agro from pet doesnt work as expected

By hinokuro, January 16

Status: Completed

**Main Category:** Core / Mangos Daemon

**Sub-Category:** Pet

Version: 22.x (Current Master Branch)Milestone: UnsetPriority: New

**Implemented Version:** Unset

When you have a pet as a hunter if u start atacking the mob and send pet even while u stop atacking the target the agro levels dont calculate and the enemie will never focus back on your pet.

## User Feedback
```

- [ ] **Step 2: Write the failing test**

`mai/tests/test_ips_parse.py`:

```python
from pathlib import Path

from mai.ips.parse import parse_bug_page, parse_bug_url

FIXTURE = (Path(__file__).parent / "fixtures" / "ips_bug_r1842.md").read_text(encoding="utf-8")
URL = ("https://www.getmangos.eu/bug-tracker/mangos-zero/"
       "agro-from-pet-doesnt-work-as-expected-r1842/")


def test_parse_bug_url_extracts_core_and_id():
    assert parse_bug_url(URL) == ("zero", "r1842")


def test_parse_bug_url_handles_nested_cross_core():
    url = ("https://www.getmangos.eu/bug-tracker/cross-core/sub-modules/scriptdev3/"
           "script-error-in-npc_prospector_anvilward-r1828/")
    assert parse_bug_url(url) == ("cross-core", "r1828")


def test_parse_bug_url_rejects_non_bug_url():
    import pytest
    with pytest.raises(ValueError):
        parse_bug_url("https://www.getmangos.eu/bug-tracker/mangos-zero/")


def test_parse_bug_page_extracts_fields():
    bug = parse_bug_page(FIXTURE)
    assert bug.title == "Agro from pet doesnt work as expected"
    assert bug.status == "Completed"
    assert bug.main_category == "Core / Mangos Daemon"
    assert bug.sub_category == "Pet"
    assert bug.version == "22.x (Current Master Branch)"
    assert bug.milestone == "Unset"
    assert bug.priority == "New"
    assert bug.implemented_version == "Unset"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd mai && pytest tests/test_ips_parse.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mai.ips'`

- [ ] **Step 4: Create `ips/__init__.py` (empty marker)**

```python
```

- [ ] **Step 5: Write `ips/parse.py`**

```python
import re
from dataclasses import dataclass

_URL_RE = re.compile(r"/bug-tracker/([^/]+)/(?:.*/)?[^/]*-r(\d+)/?$")


def parse_bug_url(url: str) -> tuple[str, str]:
    """Return (core, 'rNNNN') for an IPS bug URL. Raises ValueError otherwise."""
    m = _URL_RE.search(url)
    if not m:
        raise ValueError(f"not an IPS bug url: {url}")
    segment, number = m.group(1), m.group(2)
    core = segment[len("mangos-"):] if segment.startswith("mangos-") else segment
    return core, f"r{number}"


@dataclass(frozen=True)
class IpsBug:
    title: str
    status: str | None
    main_category: str | None
    sub_category: str | None
    version: str | None
    milestone: str | None
    priority: str | None
    implemented_version: str | None


def _find(pattern: str, text: str, flags: int = 0) -> str | None:
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else None


def parse_bug_page(markdown: str) -> IpsBug:
    """Best-effort extraction of the labeled IPS fields from page markdown."""
    return IpsBug(
        title=_find(r"^#\s+(.+)$", markdown, re.M) or "",
        status=_find(r"Status:\s*([A-Za-z][A-Za-z ]*)", markdown),
        main_category=_find(r"Main Category:\*{0,2}\s*(.+)", markdown),
        sub_category=_find(r"Sub-Category:\*{0,2}\s*(.+)", markdown),
        version=_find(
            r"(?<!Implemented )Version:\*{0,2}\s*(.+?)\s*(?:Milestone:|Priority:|$)",
            markdown,
        ),
        milestone=_find(r"Milestone:\s*(\w+)", markdown),
        priority=_find(r"Priority:\s*(\w+)", markdown),
        implemented_version=_find(r"Implemented Version:\*{0,2}\s*(\w+)", markdown),
    )
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd mai && pytest tests/test_ips_parse.py -v`
Expected: PASS (4 passed)

- [ ] **Step 7: Commit**

```bash
git add mai/tests/fixtures/ips_bug_r1842.md mai/tests/test_ips_parse.py mai/src/mai/ips/__init__.py mai/src/mai/ips/parse.py
git commit -m "feat: parse IPS bug URLs and detail-page fields"
```

---

### Task 2: Normalize IPS bug to IntakeEvent

**Files:**
- Create: `mai/src/mai/ips/normalize.py`
- Create: `mai/tests/test_ips_normalize.py`

- [ ] **Step 1: Write the failing test**

`mai/tests/test_ips_normalize.py`:

```python
from pathlib import Path

from mai.contracts import IntakeEvent
from mai.ips.normalize import normalize_ips

FIXTURE = (Path(__file__).parent / "fixtures" / "ips_bug_r1842.md").read_text(encoding="utf-8")
URL = ("https://www.getmangos.eu/bug-tracker/mangos-zero/"
       "agro-from-pet-doesnt-work-as-expected-r1842/")


def test_normalize_ips_builds_intake_event():
    evt = normalize_ips(URL, FIXTURE)
    assert isinstance(evt, IntakeEvent)
    assert evt.source_type == "ips"
    assert evt.source_id == "r1842"
    assert evt.canonical_key() == "ips:r1842"
    assert evt.core == "zero"
    assert evt.status == "completed"  # lowercased source status
    assert evt.title == "Agro from pet doesnt work as expected"
    assert evt.repo_full_name is None


def test_normalize_ips_preserves_raw_and_parsed_fields():
    evt = normalize_ips(URL, FIXTURE)
    assert evt.raw_payload["url"] == URL
    assert evt.raw_payload["markdown"] == FIXTURE  # full page preserved (raw is sacred)
    assert evt.raw_payload["sub_category"] == "Pet"
    assert evt.raw_payload["priority"] == "New"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mai && pytest tests/test_ips_normalize.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mai.ips.normalize'`

- [ ] **Step 3: Write `ips/normalize.py`**

```python
from mai.contracts import IntakeEvent
from mai.ips.parse import parse_bug_page, parse_bug_url

SOURCE_IPS = "ips"


def normalize_ips(url: str, markdown: str) -> IntakeEvent:
    core, bug_id = parse_bug_url(url)
    bug = parse_bug_page(markdown)
    status = (bug.status or "open").strip().lower()
    return IntakeEvent(
        source_type=SOURCE_IPS,
        source_id=bug_id,
        title=bug.title,
        core=core,
        status=status,
        repo_full_name=None,
        raw_payload={
            "url": url,
            "markdown": markdown,
            "main_category": bug.main_category,
            "sub_category": bug.sub_category,
            "version": bug.version,
            "milestone": bug.milestone,
            "priority": bug.priority,
            "implemented_version": bug.implemented_version,
        },
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd mai && pytest tests/test_ips_normalize.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add mai/src/mai/ips/normalize.py mai/tests/test_ips_normalize.py
git commit -m "feat: normalize IPS bug pages to IntakeEvent (raw markdown preserved)"
```

---

### Task 3: Crawl orchestration + FakeIpsClient

**Files:**
- Create: `mai/src/mai/ips/client.py` (protocol only in this task)
- Create: `mai/src/mai/ips/fake.py`
- Create: `mai/src/mai/ips_crawl.py`
- Create: `mai/tests/test_ips_crawl.py`

- [ ] **Step 1: Write the `IpsClient` protocol in `ips/client.py`**

(The `FirecrawlIpsClient` implementation is added in Task 4.)

```python
from typing import Protocol


class IpsClient(Protocol):
    async def list_bug_urls(self) -> list[str]: ...
    async def fetch_bug(self, url: str) -> str: ...
```

- [ ] **Step 2: Write `ips/fake.py`**

```python
class FakeIpsClient:
    """In-memory IpsClient for tests."""

    def __init__(self, urls: list[str], pages: dict[str, str]):
        self._urls = list(urls)
        self._pages = dict(pages)

    async def list_bug_urls(self) -> list[str]:
        return list(self._urls)

    async def fetch_bug(self, url: str) -> str:
        return self._pages[url]
```

- [ ] **Step 3: Write the failing test**

`mai/tests/test_ips_crawl.py`:

```python
from pathlib import Path

from sqlalchemy import func, select

from mai.db.models import Report, SourceRecord
from mai.ips.fake import FakeIpsClient
from mai.ips_crawl import crawl_all

FIXTURE = (Path(__file__).parent / "fixtures" / "ips_bug_r1842.md").read_text(encoding="utf-8")
URL1 = ("https://www.getmangos.eu/bug-tracker/mangos-zero/"
        "agro-from-pet-doesnt-work-as-expected-r1842/")
URL2 = "https://www.getmangos.eu/bug-tracker/mangos-three/night-elf-1-10-r1861/"
PAGE2 = "# Night Elf 1 - 10\n\nStatus: New\n\n**Main Category:** Core\n"


async def test_crawl_all_ingests_each_bug(session):
    client = FakeIpsClient(urls=[URL1, URL2], pages={URL1: FIXTURE, URL2: PAGE2})
    n = await crawl_all(session, client)
    assert n == 2
    assert await session.scalar(select(func.count()).select_from(SourceRecord)) == 2
    keys = set(await session.scalars(select(Report.canonical_key)))
    assert keys == {"ips:r1842", "ips:r1861"}


async def test_crawl_all_is_idempotent(session):
    client = FakeIpsClient(urls=[URL1, URL2], pages={URL1: FIXTURE, URL2: PAGE2})
    await crawl_all(session, client)
    await crawl_all(session, client)
    assert await session.scalar(select(func.count()).select_from(SourceRecord)) == 2
```

- [ ] **Step 4: Run test to verify it fails**

Run: `cd mai && pytest tests/test_ips_crawl.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mai.ips_crawl'`

- [ ] **Step 5: Write `ips_crawl.py`**

```python
from sqlalchemy.ext.asyncio import AsyncSession

from mai.ingest import ingest_event
from mai.ips.client import IpsClient
from mai.ips.normalize import normalize_ips


async def crawl_all(session: AsyncSession, client: IpsClient) -> int:
    """Discover all bug URLs, fetch + ingest each. Commits per bug (resumable)."""
    urls = await client.list_bug_urls()
    count = 0
    for url in urls:
        markdown = await client.fetch_bug(url)
        await ingest_event(session, normalize_ips(url, markdown))
        await session.commit()
        count += 1
    return count
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd mai && pytest tests/test_ips_crawl.py -v`
Expected: PASS (2 passed)

- [ ] **Step 7: Commit**

```bash
git add mai/src/mai/ips/client.py mai/src/mai/ips/fake.py mai/src/mai/ips_crawl.py mai/tests/test_ips_crawl.py
git commit -m "feat: crawl_all orchestration + FakeIpsClient (per-bug resumable commit)"
```

---

### Task 4: FirecrawlIpsClient (real httpx client)

**Files:**
- Modify: `mai/src/mai/ips/client.py` (add `FirecrawlIpsClient`)
- Create: `mai/tests/test_ips_client.py`

- [ ] **Step 1: Write the failing test**

`mai/tests/test_ips_client.py`:

```python
import httpx

from mai.ips.client import FirecrawlIpsClient

BUG = "https://www.getmangos.eu/bug-tracker/mangos-zero/agro-x-r1842/"


def _handler(request: httpx.Request) -> httpx.Response:
    assert request.headers["Authorization"] == "Bearer fc-key"
    if request.url.path == "/v1/map":
        return httpx.Response(200, json={"success": True, "links": [
            BUG,
            "https://www.getmangos.eu/profile/1-someone/",
            "https://www.getmangos.eu/bug-tracker/mangos-zero/",
        ]})
    if request.url.path == "/v1/scrape":
        return httpx.Response(200, json={"success": True,
                                         "data": {"markdown": "# T\n\nStatus: New\n"}})
    return httpx.Response(404, json={})


async def test_list_bug_urls_filters_to_bug_pages():
    transport = httpx.MockTransport(_handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = FirecrawlIpsClient("fc-key", client=http)
        urls = await client.list_bug_urls()
    assert urls == [BUG]  # profile + category links filtered out


async def test_fetch_bug_returns_markdown():
    transport = httpx.MockTransport(_handler)
    async with httpx.AsyncClient(transport=transport) as http:
        client = FirecrawlIpsClient("fc-key", client=http)
        md = await client.fetch_bug(BUG)
    assert md.startswith("# T")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mai && pytest tests/test_ips_client.py -v`
Expected: FAIL with `ImportError: cannot import name 'FirecrawlIpsClient'`

- [ ] **Step 3: Add `FirecrawlIpsClient` to `ips/client.py`**

Append to `mai/src/mai/ips/client.py` (keep the `IpsClient` protocol; add the imports at top):

```python
import re

import httpx

_BUG_URL_RE = re.compile(r"-r\d+/?$")


class FirecrawlIpsClient:
    """Production IpsClient backed by the Firecrawl API (map + scrape)."""

    def __init__(self, api_key: str,
                 base_url: str = "https://api.firecrawl.dev",
                 bug_tracker_url: str = "https://www.getmangos.eu/bug-tracker/",
                 client: httpx.AsyncClient | None = None):
        self._base = base_url.rstrip("/")
        self._tracker = bug_tracker_url
        # caller should inject a managed client (async with ...) in production
        self._client = client or httpx.AsyncClient()
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    async def list_bug_urls(self) -> list[str]:
        resp = await self._client.post(
            self._base + "/v1/map",
            json={"url": self._tracker},
            headers=self._headers,
        )
        resp.raise_for_status()
        links = resp.json().get("links", [])
        return [u for u in links if _BUG_URL_RE.search(u)]

    async def fetch_bug(self, url: str) -> str:
        resp = await self._client.post(
            self._base + "/v1/scrape",
            json={"url": url, "formats": ["markdown"], "onlyMainContent": True},
            headers=self._headers,
        )
        resp.raise_for_status()
        return resp.json().get("data", {}).get("markdown", "")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd mai && pytest tests/test_ips_client.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add mai/src/mai/ips/client.py mai/tests/test_ips_client.py
git commit -m "feat: FirecrawlIpsClient (map discovery + scrape, MockTransport-tested)"
```

---

### Task 5: CLI wiring (ips-crawl) and full-suite green

**Files:**
- Modify: `mai/src/mai/config.py` (add Firecrawl/IPS settings)
- Modify: `mai/src/mai/cli/__main__.py` (add ips-crawl subcommand)

- [ ] **Step 1: Add settings to `config.py`**

In `mai/src/mai/config.py`, add three fields to `Settings` (below the github fields):

```python
    firecrawl_api_key: str | None = None
    firecrawl_api_url: str = "https://api.firecrawl.dev"
    ips_bug_tracker_url: str = "https://www.getmangos.eu/bug-tracker/"
```

- [ ] **Step 2: Add the `ips-crawl` subcommand to `cli/__main__.py`**

Add this coroutine (after `_harvest`):

```python
async def _ips_crawl() -> int:
    if not settings.firecrawl_api_key:
        raise SystemExit("FIRECRAWL_API_KEY not set")
    import httpx

    from mai.ips.client import FirecrawlIpsClient
    from mai.ips_crawl import crawl_all

    async with httpx.AsyncClient(timeout=60.0) as http:
        client = FirecrawlIpsClient(
            settings.firecrawl_api_key,
            base_url=settings.firecrawl_api_url,
            bug_tracker_url=settings.ips_bug_tracker_url,
            client=http,
        )
        async with SessionFactory() as session:
            return await crawl_all(session, client)
```

Then register + dispatch the subcommand in `main()`. Add `sub.add_parser("ips-crawl")` alongside the others, and add this branch to the dispatch chain:

```python
    elif args.cmd == "ips-crawl":
        count = asyncio.run(_ips_crawl())
        print(f"crawled {count} bugs")
```

- [ ] **Step 3: Run the full test suite**

Run: `cd mai && pytest -q`
Expected: PASS (28 passed — 18 prior + 10 new).

- [ ] **Step 4: Verify the CLI registers the new subcommand**

Run: `cd mai && python -m mai.cli.__main__ ips-crawl` (with `PYTHONPATH=src` if needed, and no `FIRECRAWL_API_KEY` set)
Expected: exits with `FIRECRAWL_API_KEY not set` (proves the subcommand is wired; the guard fires before any network call).

- [ ] **Step 5: (Optional, needs a Firecrawl key) Live crawl smoke test**

If a Firecrawl API key is available:
```bash
cd mai && FIRECRAWL_API_KEY=<key> python -m mai.cli.__main__ ips-crawl
```
Expected: prints `crawled N bugs` (N in the hundreds/thousands). If no key, SKIP and note it — crawl logic is already covered by `test_ips_crawl.py` with the fake client.

- [ ] **Step 6: Commit**

```bash
git add mai/src/mai/config.py mai/src/mai/cli/__main__.py
git commit -m "feat: mai CLI ips-crawl subcommand"
```

---

## Self-Review

- **Spec coverage:** Implements the IPS side of spec §7 stage 2 (Firecrawl crawl, `rNNNN` keys) and §3's verified findings (rich IPS field set captured; full page preserved so the addon's future structured fields flow in via raw JSONB without migration). Status is carried verbatim (lowercased), NOT interpreted as resolution — resolution is a computed verdict in Plan 04, per §6.
- **Invariants:** immutable IDs (`rNNNN`, IPS-global) ✓ · append-only raw incl. full markdown ✓ · one ingestion contract (`IntakeEvent`) ✓ · repository seam (reuses `ingest_event`) ✓ · replayable (raw markdown retained) ✓.
- **Placeholder scan:** none — every step has runnable code/commands.
- **Type consistency:** `IpsClient.list_bug_urls()/fetch_bug(url)` signatures match both `FakeIpsClient` and `FirecrawlIpsClient`; `parse_bug_url` returns `(core, "rNNNN")` used identically by `normalize_ips`; `canonical_key()` form `ips:rNNNN` matches the crawl-test assertions and the Plan 01 markdown fixture.

## Notes for later plans

- **Incremental re-crawl (deferred):** v1 re-crawls all bugs (idempotent via content_hash). Spec §7's "re-scrape only rows whose last-comment date changed" needs a listing-page diff pass + a cursor; add when crawl cost matters.
- **Authed crawl (deferred):** the tracker is publicly readable, so v1 is unauthenticated. Login-gated fields/comments will need the getmangos session cookie passed via Firecrawl `headers` — spec §10.
- **Parser robustness:** `parse_bug_page` is best-effort regex against page markdown; if Firecrawl output drifts, extend patterns. Raw markdown is always retained, so re-parsing is a recompute, never a re-fetch.
- **Status vocabulary:** statuses are stored verbatim-lowercased; Plan 04 maps them + code evidence into the canonical verdict.
