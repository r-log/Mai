# Mai AI Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the AI enrichment layer — pass each raw bug report (often sparse / non-English) to a model that returns a clean, structured, English, schema-validated version (normalized title, summary, repro steps, affected game entities, language, severity, clarity, needs-review), stored as a **derived, recomputable** record beside the immutable raw, with full provenance and cost-saving caching.

**Architecture:** An `Enricher` protocol abstracts the model so orchestration is testable without network: `FakeEnricher` (deterministic) for tests, `OpenRouterEnricher` (httpx → OpenRouter chat completions) for production. The model MUST return JSON matching a pydantic schema (`EnrichmentResult`) or it's rejected (`EnrichmentSchemaError`) — GITA's guardrail pattern. `enrich_report` builds an input from the report's latest raw source, computes a content-hash cache key, skips if an enrichment for `(report, model, prompt_version, schema_version, input_hash)` already exists, else calls the enricher and stores a derived `Enrichment` row + an `enriched` event. Raw is never touched; the original report fields stay intact.

**Tech Stack:** Python 3.12 · pydantic v2 · httpx · SQLAlchemy 2.0 async · pytest + pytest-asyncio · `httpx.MockTransport`.

---

## Builds on Plans 01–03

Reuse as-is (do NOT redefine):
- `mai.contracts.IntakeEvent`, `mai.ingest.ingest_event`.
- `mai.db.models` — `Report`, `SourceRecord`, `ReportSourceMap`, `Event`, helpers `_uuid`/`_now`.
- `mai.repository.reports.ReportRepository` (has `.get_report(key)`, `.all_reports()`).
- `tests/conftest.py` `session` fixture; config/CLI patterns.

**Design principles enforced here:**
- Enrichment is **derived** (invariant 2): never mutate `source_record` or the report's own fields; original + enriched coexist.
- **Provenance + re-runnability:** every enrichment records `model`, `prompt_version`, `schema_version`, `input_hash`; bumping a version re-enriches; the cache key avoids re-paying for unchanged reports.
- **Anti-hallucination:** the system prompt forbids inventing facts and sets `needs_human_review` when the source is too thin.
- Status from sources is NOT resolution; enrichment does NOT decide "fixed" (that's Plan 05).

## File Structure

```
src/mai/
  config.py                       # MODIFY: openrouter_api_key/url, enrichment_model
  db/models.py                    # MODIFY: Boolean import + Enrichment model
  enrich/
    __init__.py                   # new (empty)
    schema.py                     # EnrichmentResult/AffectedEntities, EnrichmentInput,
                                  #   parse_enrichment, EnrichmentSchemaError, helpers
    prompt.py                     # SYSTEM_PROMPT, PROMPT_VERSION, build_prompt
    enricher.py                   # Enricher protocol + OpenRouterEnricher
    fake.py                       # FakeEnricher (tests)
  enrich_run.py                   # enrich_report / enrich_pending orchestration
  repository/enrichment.py        # EnrichmentRepository (seam)
  cli/__main__.py                 # MODIFY: add enrich subcommand
tests/
  test_enrichment_schema.py
  test_enrichment_repo.py
  test_enrich_run.py
  test_openrouter_enricher.py
```

---

### Task 1: Enrichment schema + validation

**Files:**
- Modify: `mai/pyproject.toml` (add pydantic explicitly)
- Create: `mai/src/mai/enrich/__init__.py`
- Create: `mai/src/mai/enrich/schema.py`
- Create: `mai/tests/test_enrichment_schema.py`

- [ ] **Step 1: Add `pydantic` to dependencies in `pyproject.toml`**

In `mai/pyproject.toml`, add `"pydantic>=2.0"` to the `dependencies` list (it's already present transitively via pydantic-settings; this makes it explicit):

```toml
dependencies = [
    "sqlalchemy>=2.0",
    "pydantic-settings>=2.0",
    "pydantic>=2.0",
    "aiosqlite>=0.19",
    "asyncpg>=0.29",
    "httpx>=0.27",
]
```

- [ ] **Step 2: Write the failing test**

`mai/tests/test_enrichment_schema.py`:

```python
import pytest

from mai.enrich.schema import (
    EnrichmentInput,
    EnrichmentResult,
    EnrichmentSchemaError,
    parse_enrichment,
    raw_text_from_payload,
)


def test_parse_enrichment_valid_minimal():
    r = parse_enrichment('{"normalized_title":"Pet threat","english_summary":"Pet loses threat."}')
    assert isinstance(r, EnrichmentResult)
    assert r.normalized_title == "Pet threat"
    assert r.needs_human_review is False
    assert r.affected_entities.npc == []
    assert r.clarity_score == 0.0


def test_parse_enrichment_full_dict():
    r = parse_enrichment({
        "normalized_title": "T", "english_summary": "S",
        "steps_to_reproduce": ["a", "b"],
        "affected_entities": {"npc": ["Devilsaur"]},
        "language_detected": "es", "severity_guess": "high",
        "clarity_score": 0.9, "needs_human_review": True,
    })
    assert r.steps_to_reproduce == ["a", "b"]
    assert r.affected_entities.npc == ["Devilsaur"]
    assert r.needs_human_review is True


def test_parse_enrichment_missing_required_raises():
    with pytest.raises(EnrichmentSchemaError):
        parse_enrichment('{"english_summary":"no title field"}')


def test_parse_enrichment_bad_json_raises():
    with pytest.raises(EnrichmentSchemaError):
        parse_enrichment("{not valid json")


def test_enrichment_input_hash_is_stable_and_content_sensitive():
    a = EnrichmentInput("t", "zero", "ips", "body")
    b = EnrichmentInput("t", "zero", "ips", "body")
    c = EnrichmentInput("t", "zero", "ips", "BODY")
    assert a.content_hash() == b.content_hash()
    assert a.content_hash() != c.content_hash()


def test_raw_text_from_payload_picks_right_field():
    assert raw_text_from_payload("ips", {"markdown": "MD"}) == "MD"
    assert raw_text_from_payload("gh_issue", {"body": "BODY"}) == "BODY"
    assert raw_text_from_payload("gh_pr", {"body": None}) == ""
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd mai && pytest tests/test_enrichment_schema.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mai.enrich'`

- [ ] **Step 4: Create `enrich/__init__.py` (empty marker)**

```python
```

- [ ] **Step 5: Write `enrich/schema.py`**

```python
import hashlib
import json
from dataclasses import dataclass

from pydantic import BaseModel, Field, ValidationError

SCHEMA_VERSION = 1


class EnrichmentSchemaError(ValueError):
    """Raised when a model's output is not valid against EnrichmentResult."""


class AffectedEntities(BaseModel):
    npc: list[str] = Field(default_factory=list)
    zone: list[str] = Field(default_factory=list)
    spell: list[str] = Field(default_factory=list)
    item: list[str] = Field(default_factory=list)
    quest: list[str] = Field(default_factory=list)


class EnrichmentResult(BaseModel):
    normalized_title: str
    english_summary: str
    steps_to_reproduce: list[str] = Field(default_factory=list)
    affected_entities: AffectedEntities = Field(default_factory=AffectedEntities)
    language_detected: str = "unknown"
    severity_guess: str = "unknown"
    clarity_score: float = 0.0
    needs_human_review: bool = False


def parse_enrichment(content: str | dict) -> EnrichmentResult:
    """Validate raw model output (JSON string or dict) into EnrichmentResult."""
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except json.JSONDecodeError as exc:
            raise EnrichmentSchemaError(f"invalid JSON: {exc}") from exc
    try:
        return EnrichmentResult.model_validate(content)
    except ValidationError as exc:
        raise EnrichmentSchemaError(str(exc)) from exc


@dataclass(frozen=True)
class EnrichmentInput:
    title: str
    core: str
    source_type: str
    raw_text: str

    def content_hash(self) -> str:
        blob = json.dumps(
            {"title": self.title, "core": self.core,
             "source_type": self.source_type, "raw_text": self.raw_text},
            sort_keys=True, separators=(",", ":"),
        )
        return hashlib.sha256(blob.encode()).hexdigest()


def raw_text_from_payload(source_type: str, payload: dict) -> str:
    """Extract the human-readable report text from a source_record payload."""
    if source_type == "ips":
        return payload.get("markdown", "") or ""
    return payload.get("body") or ""
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd mai && pytest tests/test_enrichment_schema.py -v`
Expected: PASS (6 passed)

- [ ] **Step 7: Commit**

```bash
git add mai/pyproject.toml mai/src/mai/enrich/__init__.py mai/src/mai/enrich/schema.py mai/tests/test_enrichment_schema.py
git commit -m "feat: enrichment schema + validation (pydantic guardrail)"
```

---

### Task 2: Enrichment model + EnrichmentRepository

**Files:**
- Modify: `mai/src/mai/db/models.py` (add Boolean import + Enrichment class)
- Create: `mai/src/mai/repository/enrichment.py`
- Create: `mai/tests/test_enrichment_repo.py`

- [ ] **Step 1: Add `Boolean` to the sqlalchemy import in `db/models.py`**

In `mai/src/mai/db/models.py`, the existing `from sqlalchemy import ...` line must include `Boolean`. Add it (alphabetically first is fine), e.g.:

```python
from sqlalchemy import (
    Boolean,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
```

(If the existing import is a single line, just insert `Boolean,` into it — do not remove any existing names.)

- [ ] **Step 2: Append the `Enrichment` model at the end of `db/models.py`**

```python
class Enrichment(Base):
    """Derived, recomputable AI-structured view of a report. Beside the raw, never over it."""
    __tablename__ = "enrichment"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    report_id: Mapped[str] = mapped_column(ForeignKey("report.id"))
    model: Mapped[str] = mapped_column(String(128))
    prompt_version: Mapped[int] = mapped_column(Integer)
    schema_version: Mapped[int] = mapped_column(Integer)
    input_hash: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    needs_human_review: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(default=_now)

    __table_args__ = (
        UniqueConstraint("report_id", "model", "prompt_version", "schema_version",
                         "input_hash", name="uq_enrichment_key"),
    )
```

- [ ] **Step 3: Write the failing test**

`mai/tests/test_enrichment_repo.py`:

```python
from sqlalchemy import func, select

from mai.contracts import IntakeEvent
from mai.db.models import Enrichment
from mai.enrich.schema import EnrichmentInput
from mai.ingest import ingest_event
from mai.repository.enrichment import EnrichmentRepository
from mai.repository.reports import ReportRepository

EVT = IntakeEvent("ips", "r1842", "Pet bug", "zero", status="new",
                  raw_payload={"markdown": "# Pet\nthreat union bug"})


async def test_build_input_extracts_latest_raw_text(session):
    await ingest_event(session, EVT)
    await session.commit()
    report = await ReportRepository(session).get_report("ips:r1842")
    ctx = await EnrichmentRepository(session).build_input(report)
    assert isinstance(ctx, EnrichmentInput)
    assert ctx.title == "Pet bug"
    assert ctx.core == "zero"
    assert ctx.source_type == "ips"
    assert "threat union bug" in ctx.raw_text


async def test_exists_false_then_add_then_true(session):
    await ingest_event(session, EVT)
    await session.commit()
    report = await ReportRepository(session).get_report("ips:r1842")
    repo = EnrichmentRepository(session)
    assert await repo.exists(report.id, "fake", 1, 1, "hash") is False
    repo.add(report_id=report.id, model="fake", prompt_version=1, schema_version=1,
             input_hash="hash", result={"normalized_title": "x"}, needs_human_review=False)
    await session.commit()
    assert await repo.exists(report.id, "fake", 1, 1, "hash") is True
    assert await session.scalar(select(func.count()).select_from(Enrichment)) == 1
```

- [ ] **Step 4: Run test to verify it fails**

Run: `cd mai && pytest tests/test_enrichment_repo.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mai.repository.enrichment'`

- [ ] **Step 5: Write `repository/enrichment.py`**

```python
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from mai.db.models import Enrichment, Event, Report, ReportSourceMap, SourceRecord
from mai.enrich.schema import EnrichmentInput, raw_text_from_payload


class EnrichmentRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def build_input(self, report: Report) -> EnrichmentInput:
        """Build the model input from the report's latest raw source record."""
        maps = list(await self._session.scalars(
            select(ReportSourceMap).where(ReportSourceMap.report_id == report.id)
        ))
        source_type = ""
        raw_text = ""
        for m in maps:
            rec = await self._session.scalar(
                select(SourceRecord)
                .where(SourceRecord.source_type == m.source_type,
                       SourceRecord.source_id == m.source_id)
                .order_by(desc(SourceRecord.version))
                .limit(1)
            )
            if rec is not None:
                source_type = rec.source_type
                raw_text = raw_text_from_payload(rec.source_type, rec.payload)
                break
        return EnrichmentInput(title=report.title, core=report.core,
                               source_type=source_type, raw_text=raw_text)

    async def exists(self, report_id: str, model: str, prompt_version: int,
                     schema_version: int, input_hash: str) -> bool:
        return bool(await self._session.scalar(
            select(Enrichment.id).where(
                Enrichment.report_id == report_id,
                Enrichment.model == model,
                Enrichment.prompt_version == prompt_version,
                Enrichment.schema_version == schema_version,
                Enrichment.input_hash == input_hash,
            )
        ))

    def add(self, **kw) -> Enrichment:
        row = Enrichment(**kw)
        self._session.add(row)
        return row

    def add_event(self, **kw) -> None:
        self._session.add(Event(**kw))
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd mai && pytest tests/test_enrichment_repo.py -v`
Expected: PASS (2 passed)

- [ ] **Step 7: Commit**

```bash
git add mai/src/mai/db/models.py mai/src/mai/repository/enrichment.py mai/tests/test_enrichment_repo.py
git commit -m "feat: Enrichment model + EnrichmentRepository (derived, cache-keyed)"
```

---

### Task 3: Enricher protocol, FakeEnricher, prompt, and orchestration

**Files:**
- Create: `mai/src/mai/enrich/prompt.py`
- Create: `mai/src/mai/enrich/enricher.py` (protocol only in this task)
- Create: `mai/src/mai/enrich/fake.py`
- Create: `mai/src/mai/enrich_run.py`
- Create: `mai/tests/test_enrich_run.py`

- [ ] **Step 1: Write `enrich/prompt.py`**

```python
from mai.enrich.schema import EnrichmentInput

PROMPT_VERSION = 1

SYSTEM_PROMPT = (
    "You are a bug-report normalizer for the MaNGOS World of Warcraft emulator "
    "project. Restructure and translate the report into clear, direct English. "
    "NEVER invent details that are not present in the source text. If the report "
    "is too sparse or ambiguous to understand, set needs_human_review to true and "
    "keep the summary strictly faithful to what is written. Only list affected "
    "entities (npc, zone, spell, item, quest) that are explicitly named in the "
    "source. Respond ONLY with a single JSON object matching the requested schema."
)


def build_prompt(ctx: EnrichmentInput) -> str:
    return (
        f"Core: {ctx.core}\n"
        f"Source: {ctx.source_type}\n"
        f"Title: {ctx.title}\n\n"
        f"Raw report:\n{ctx.raw_text}\n\n"
        "Return a JSON object with keys: normalized_title, english_summary, "
        "steps_to_reproduce (list), affected_entities (object with npc, zone, "
        "spell, item, quest lists), language_detected, severity_guess "
        "(low|medium|high|unknown), clarity_score (0.0-1.0), needs_human_review "
        "(boolean)."
    )
```

- [ ] **Step 2: Write `enrich/enricher.py` (protocol only; OpenRouterEnricher added in Task 4)**

```python
from typing import Protocol

from mai.enrich.schema import EnrichmentInput, EnrichmentResult


class Enricher(Protocol):
    @property
    def model(self) -> str: ...

    async def enrich(self, ctx: EnrichmentInput) -> EnrichmentResult: ...
```

- [ ] **Step 3: Write `enrich/fake.py`**

```python
from mai.enrich.schema import EnrichmentInput, EnrichmentResult


class FakeEnricher:
    """Deterministic Enricher for tests. Counts calls so caching can be asserted."""

    def __init__(self, result: EnrichmentResult | None = None, model: str = "fake"):
        self._result = result or EnrichmentResult(
            normalized_title="Norm", english_summary="Sum")
        self._model = model
        self.calls = 0

    @property
    def model(self) -> str:
        return self._model

    async def enrich(self, ctx: EnrichmentInput) -> EnrichmentResult:
        self.calls += 1
        return self._result
```

- [ ] **Step 4: Write the failing test**

`mai/tests/test_enrich_run.py`:

```python
from sqlalchemy import func, select

from mai.contracts import IntakeEvent
from mai.db.models import Enrichment, Event
from mai.enrich.fake import FakeEnricher
from mai.enrich.schema import EnrichmentResult
from mai.enrich_run import enrich_pending, enrich_report
from mai.ingest import ingest_event
from mai.repository.reports import ReportRepository

EVT = IntakeEvent("ips", "r1842", "Pet bug", "zero", status="new",
                  raw_payload={"markdown": "threat bug"})


async def test_enrich_report_creates_enrichment_and_event(session):
    await ingest_event(session, EVT)
    await session.commit()
    report = await ReportRepository(session).get_report("ips:r1842")
    enricher = FakeEnricher(EnrichmentResult(
        normalized_title="Pet threat", english_summary="Pet loses threat",
        needs_human_review=True))
    created = await enrich_report(session, enricher, report)
    await session.commit()
    assert created is True
    assert enricher.calls == 1
    row = await session.scalar(select(Enrichment))
    assert row.result["normalized_title"] == "Pet threat"
    assert row.needs_human_review is True
    assert await session.scalar(
        select(func.count()).select_from(Event).where(Event.kind == "enriched")) == 1


async def test_enrich_report_is_cached_on_second_call(session):
    await ingest_event(session, EVT)
    await session.commit()
    report = await ReportRepository(session).get_report("ips:r1842")
    enricher = FakeEnricher()
    assert await enrich_report(session, enricher, report) is True
    await session.commit()
    assert await enrich_report(session, enricher, report) is False
    assert enricher.calls == 1


async def test_enrich_pending_enriches_all_then_none(session):
    await ingest_event(session, EVT)
    await ingest_event(session, IntakeEvent("ips", "r2", "Other", "two", status="new",
                                            raw_payload={"markdown": "x"}))
    await session.commit()
    enricher = FakeEnricher()
    assert await enrich_pending(session, enricher) == 2
    assert await enrich_pending(session, enricher) == 0
```

- [ ] **Step 5: Run test to verify it fails**

Run: `cd mai && pytest tests/test_enrich_run.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mai.enrich_run'`

- [ ] **Step 6: Write `enrich_run.py`**

```python
from sqlalchemy.ext.asyncio import AsyncSession

from mai.db.models import Report
from mai.enrich.enricher import Enricher
from mai.enrich.prompt import PROMPT_VERSION
from mai.enrich.schema import SCHEMA_VERSION
from mai.repository.enrichment import EnrichmentRepository
from mai.repository.reports import ReportRepository


async def enrich_report(session: AsyncSession, enricher: Enricher, report: Report) -> bool:
    """Enrich one report unless an identical enrichment already exists. Returns True if created."""
    repo = EnrichmentRepository(session)
    ctx = await repo.build_input(report)
    input_hash = ctx.content_hash()
    if await repo.exists(report.id, enricher.model, PROMPT_VERSION, SCHEMA_VERSION, input_hash):
        return False
    result = await enricher.enrich(ctx)
    repo.add(report_id=report.id, model=enricher.model, prompt_version=PROMPT_VERSION,
             schema_version=SCHEMA_VERSION, input_hash=input_hash,
             result=result.model_dump(), needs_human_review=result.needs_human_review)
    repo.add_event(report_id=report.id, kind="enriched",
                   payload={"model": enricher.model, "prompt_version": PROMPT_VERSION})
    return True


async def enrich_pending(session: AsyncSession, enricher: Enricher) -> int:
    """Enrich every report lacking a current enrichment. Commits per report (resumable)."""
    reports = await ReportRepository(session).all_reports()
    count = 0
    for report in reports:
        if await enrich_report(session, enricher, report):
            count += 1
        await session.commit()
    return count
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd mai && pytest tests/test_enrich_run.py -v`
Expected: PASS (3 passed)

- [ ] **Step 8: Commit**

```bash
git add mai/src/mai/enrich/prompt.py mai/src/mai/enrich/enricher.py mai/src/mai/enrich/fake.py mai/src/mai/enrich_run.py mai/tests/test_enrich_run.py
git commit -m "feat: Enricher protocol + FakeEnricher + enrich orchestration (cached, provenance)"
```

---

### Task 4: OpenRouterEnricher (real httpx client)

**Files:**
- Modify: `mai/src/mai/enrich/enricher.py` (add `OpenRouterEnricher`)
- Create: `mai/tests/test_openrouter_enricher.py`

- [ ] **Step 1: Write the failing test**

`mai/tests/test_openrouter_enricher.py`:

```python
import httpx
import pytest

from mai.enrich.enricher import OpenRouterEnricher
from mai.enrich.schema import EnrichmentInput, EnrichmentResult, EnrichmentSchemaError

CTX = EnrichmentInput(title="Pet bug", core="zero", source_type="ips", raw_text="threat bug")
GOOD = ('{"normalized_title":"Pet threat","english_summary":"Pet loses threat",'
        '"language_detected":"en"}')


def _ok(request: httpx.Request) -> httpx.Response:
    assert request.headers["Authorization"] == "Bearer or-key"
    assert request.url.path == "/v1/chat/completions"
    return httpx.Response(200, json={"choices": [{"message": {"content": GOOD}}]})


def _bad_json(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": "{not json"}}]})


async def test_openrouter_enricher_returns_validated_result():
    async with httpx.AsyncClient(transport=httpx.MockTransport(_ok)) as http:
        enricher = OpenRouterEnricher("or-key", "some/model", client=http)
        result = await enricher.enrich(CTX)
    assert isinstance(result, EnrichmentResult)
    assert result.normalized_title == "Pet threat"
    assert result.language_detected == "en"
    assert enricher.model == "some/model"


async def test_openrouter_enricher_raises_on_bad_json():
    async with httpx.AsyncClient(transport=httpx.MockTransport(_bad_json)) as http:
        enricher = OpenRouterEnricher("or-key", "some/model", client=http)
        with pytest.raises(EnrichmentSchemaError):
            await enricher.enrich(CTX)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mai && pytest tests/test_openrouter_enricher.py -v`
Expected: FAIL with `ImportError: cannot import name 'OpenRouterEnricher'`

- [ ] **Step 3: Add `OpenRouterEnricher` to `enrich/enricher.py`**

Append to `mai/src/mai/enrich/enricher.py` (keep the `Enricher` protocol; add the imports at top):

```python
import httpx

from mai.enrich.prompt import SYSTEM_PROMPT, build_prompt
from mai.enrich.schema import parse_enrichment


class OpenRouterEnricher:
    """Production Enricher backed by OpenRouter chat completions."""

    def __init__(self, api_key: str, model: str,
                 base_url: str = "https://openrouter.ai/api",
                 client: httpx.AsyncClient | None = None):
        self._model = model
        self._base = base_url.rstrip("/")
        # caller should inject a managed client (async with ...) in production
        self._client = client or httpx.AsyncClient()
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    @property
    def model(self) -> str:
        return self._model

    async def enrich(self, ctx: EnrichmentInput) -> EnrichmentResult:
        resp = await self._client.post(
            self._base + "/v1/chat/completions",
            json={
                "model": self._model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": build_prompt(ctx)},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0,
            },
            headers=self._headers,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        return parse_enrichment(content)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd mai && pytest tests/test_openrouter_enricher.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add mai/src/mai/enrich/enricher.py mai/tests/test_openrouter_enricher.py
git commit -m "feat: OpenRouterEnricher (schema-validated, MockTransport-tested)"
```

---

### Task 5: CLI wiring (enrich) and full-suite green

**Files:**
- Modify: `mai/src/mai/config.py` (add OpenRouter settings)
- Modify: `mai/src/mai/cli/__main__.py` (add enrich subcommand)

- [ ] **Step 1: Add settings to `config.py`**

In `mai/src/mai/config.py`, add three fields to `Settings` (below the firecrawl fields):

```python
    openrouter_api_key: str | None = None
    openrouter_api_url: str = "https://openrouter.ai/api"
    enrichment_model: str = "moonshotai/kimi-k2.5"
```

- [ ] **Step 2: Add the `enrich` subcommand to `cli/__main__.py`**

Add this coroutine (after `_ips_crawl`):

```python
async def _enrich() -> int:
    if not settings.openrouter_api_key:
        raise SystemExit("OPENROUTER_API_KEY not set")
    import httpx

    from mai.enrich.enricher import OpenRouterEnricher
    from mai.enrich_run import enrich_pending

    async with httpx.AsyncClient(timeout=120.0) as http:
        enricher = OpenRouterEnricher(
            settings.openrouter_api_key,
            settings.enrichment_model,
            base_url=settings.openrouter_api_url,
            client=http,
        )
        async with SessionFactory() as session:
            return await enrich_pending(session, enricher)
```

Register the parser (`sub.add_parser("enrich")`) and add this dispatch branch:

```python
    elif args.cmd == "enrich":
        count = asyncio.run(_enrich())
        print(f"enriched {count} reports")
```

- [ ] **Step 3: Run the full test suite**

Run: `cd mai && pytest -q`
Expected: PASS (41 passed — 28 prior + 13 new).

- [ ] **Step 4: Verify the `enrich` subcommand is wired (guard fires without a key)**

Run: `cd mai && python -m mai.cli.__main__ enrich` (with `PYTHONPATH=src` if needed, no `OPENROUTER_API_KEY` set)
Expected: exits with `OPENROUTER_API_KEY not set`.

- [ ] **Step 5: (Optional, needs a key) Live enrich smoke test**

If `OPENROUTER_API_KEY` is available and some reports have been ingested:
```bash
cd mai && OPENROUTER_API_KEY=<key> python -m mai.cli.__main__ enrich
```
Expected: prints `enriched N reports`. If no key, SKIP and note it — orchestration is covered by `test_enrich_run.py` with the fake.

- [ ] **Step 6: Commit**

```bash
git add mai/src/mai/config.py mai/src/mai/cli/__main__.py
git commit -m "feat: mai CLI enrich subcommand"
```

---

## Self-Review

- **Spec coverage:** Implements the AI-enrichment half of spec §7 stage 3 — clean/translate/structure sparse reports into a derived, schema-validated record with provenance. Feeds Plan 05 (embeddings + correlation), which will embed `english_summary` and use `affected_entities` for subsystem signals.
- **Invariants:** derived & recomputable (Enrichment never touches raw/report fields) ✓ · provenance (model/prompt_version/schema_version/input_hash) + cost-saving cache key ✓ · one validated contract (`EnrichmentResult` via pydantic guardrail) ✓ · pluggable enricher (Fake/OpenRouter behind `Enricher`) ✓ · repository seam (`EnrichmentRepository`) ✓ · resumable (commit per report) ✓.
- **Anti-hallucination:** `SYSTEM_PROMPT` forbids invention and mandates `needs_human_review` on sparse input; entities limited to those named in source. (Grounding extracted entities against real mangos game data is a future enhancement — noted below.)
- **Placeholder scan:** none — every step has runnable code/commands.
- **Type consistency:** `Enricher.model` (property) + `enrich(ctx: EnrichmentInput) -> EnrichmentResult` match both `FakeEnricher` and `OpenRouterEnricher`; `enrich_report` cache key tuple `(report_id, model, PROMPT_VERSION, SCHEMA_VERSION, input_hash)` matches `EnrichmentRepository.exists` and the `uq_enrichment_key` constraint; `result.model_dump()` (pydantic) stored in the `result` JSON column.

## Notes for later plans

- **Entity grounding (future):** cross-check `affected_entities` against real mangos DBC/game data (NPC/zone/spell names) to reject hallucinated entities and add ids — the strongest anti-hallucination lever.
- **Publish (later):** surface `normalized_title` / `english_summary` / `needs_human_review` in the `.md` front-matter so the Hugo dashboard shows the clean version with the original linked.
- **Embeddings (Plan 05):** embed `english_summary` (clean English) rather than raw sparse/foreign text for better correlation similarity.
- **Migrations:** `Enrichment` still relies on `Base.metadata.create_all`; the Postgres deploy plan introduces Alembic + a baseline.
- **Cost controls:** `enrich_pending` re-checks every report each run (cheap, cached). A future `--limit`/batch flag bounds spend per run on the full ~2,600-report set.
