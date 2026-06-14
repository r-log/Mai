# Mai Embeddings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the embeddings layer — embed each report's *enriched* English text into a vector (via an OpenAI-compatible API), store it as a derived, cache-keyed record, and provide a cosine-similarity query so Plan 06 (correlation) can find the reports/PRs most similar to a given one.

**Architecture:** An `Embedder` protocol abstracts the API so orchestration is testable without network: `FakeEmbedder` (deterministic) for tests, `HttpEmbedder` (httpx → `/v1/embeddings`) for production. Vectors are stored as JSON lists of floats in an `embedding` table and cosine similarity is computed in Python — so this plan needs **no pgvector and no Postgres** (pgvector is a later performance swap behind the repository seam; invariant 5). `embed_report` prefers the report's enriched `english_summary` (Plan 04) as the embed text, caches by `(report, model, input_hash)`, and `most_similar` ranks stored vectors against a query vector.

**Tech Stack:** Python 3.12 · httpx · SQLAlchemy 2.0 async · pytest + pytest-asyncio · `httpx.MockTransport` · stdlib `math`/`hashlib`.

---

## Builds on Plans 01–04

Reuse as-is (do NOT redefine):
- `mai.db.models` — `Report`, `Enrichment`, helpers `_uuid`/`_now`; `mai.repository.reports.ReportRepository` (`.all_reports()`).
- `tests/conftest.py` `session` fixture; config/CLI patterns.

**Design principles enforced here:**
- Embedding is **derived & recomputable** (invariant 2): never mutates raw/report/enrichment.
- **Provenance + cache key:** `(report_id, model, input_hash)`; re-embed only when the embed text or model changes.
- **Store-swappable (invariant 5):** vectors are JSON now; a pgvector column + index can replace this behind `EmbeddingRepository` without touching callers.
- Embeds the **clean enriched text** when available (the reason enrichment was sequenced first), else the title.

## File Structure

```
src/mai/
  config.py                    # MODIFY: embedding_api_key/url/model/dimensions
  db/models.py                 # MODIFY: add Embedding
  embed/
    __init__.py                # new (empty)
    similarity.py              # cosine
    embedder.py                # Embedder protocol + HttpEmbedder
    fake.py                    # FakeEmbedder (tests)
  embed_run.py                 # embed_report / embed_pending / most_similar
  repository/embeddings.py     # EmbeddingRepository (seam)
  cli/__main__.py              # MODIFY: add embed subcommand
tests/
  test_similarity.py
  test_embedding_repo.py
  test_embed_run.py
  test_http_embedder.py
```

---

### Task 1: Cosine similarity

**Files:**
- Create: `mai/src/mai/embed/__init__.py`
- Create: `mai/src/mai/embed/similarity.py`
- Create: `mai/tests/test_similarity.py`

- [ ] **Step 1: Write the failing test**

`mai/tests/test_similarity.py`:

```python
import pytest

from mai.embed.similarity import cosine


def test_cosine_identical_vectors_is_one():
    assert cosine([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)


def test_cosine_orthogonal_is_zero():
    assert cosine([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_zero_vector_is_zero():
    assert cosine([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_cosine_is_scale_invariant():
    assert cosine([1.0, 1.0], [2.0, 2.0]) == pytest.approx(1.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mai && pytest tests/test_similarity.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mai.embed'`

- [ ] **Step 3: Create `embed/__init__.py` (empty marker)**

```python
```

- [ ] **Step 4: Write `embed/similarity.py`**

```python
import math


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two equal-length vectors. Returns 0.0 if either is zero."""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd mai && pytest tests/test_similarity.py -v`
Expected: PASS (4 passed)

- [ ] **Step 6: Commit**

```bash
git add mai/src/mai/embed/__init__.py mai/src/mai/embed/similarity.py mai/tests/test_similarity.py
git commit -m "feat: cosine similarity helper"
```

---

### Task 2: Embedding model + EmbeddingRepository

**Files:**
- Modify: `mai/src/mai/db/models.py` (append `Embedding`)
- Create: `mai/src/mai/repository/embeddings.py`
- Create: `mai/tests/test_embedding_repo.py`

- [ ] **Step 1: Append the `Embedding` model at the end of `db/models.py`**

(The imports `String`, `Integer`, `ForeignKey`, `UniqueConstraint`, `JSON`, `datetime`, `_uuid`, `_now` already exist in that file.)

```python
class Embedding(Base):
    """Derived vector for a report's embed-text. Stored as JSON (pgvector swap later)."""
    __tablename__ = "embedding"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    report_id: Mapped[str] = mapped_column(ForeignKey("report.id"))
    model: Mapped[str] = mapped_column(String(128))
    dimensions: Mapped[int] = mapped_column(Integer)
    input_hash: Mapped[str] = mapped_column(String(64))
    vector: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(default=_now)

    __table_args__ = (
        UniqueConstraint("report_id", "model", "input_hash", name="uq_embedding_key"),
    )
```

- [ ] **Step 2: Write the failing test**

`mai/tests/test_embedding_repo.py`:

```python
from sqlalchemy import func, select

from mai.contracts import IntakeEvent
from mai.db.models import Embedding
from mai.enrich.fake import FakeEnricher
from mai.enrich.schema import EnrichmentResult
from mai.enrich_run import enrich_report
from mai.ingest import ingest_event
from mai.repository.embeddings import EmbeddingRepository
from mai.repository.reports import ReportRepository

EVT = IntakeEvent("ips", "r1842", "Pet bug", "zero", status="new",
                  raw_payload={"markdown": "threat bug"})


async def test_build_text_prefers_enrichment_summary(session):
    await ingest_event(session, EVT)
    await session.commit()
    report = await ReportRepository(session).get_report("ips:r1842")
    enricher = FakeEnricher(EnrichmentResult(
        normalized_title="Pet threat", english_summary="Pet loses threat after attack."))
    await enrich_report(session, enricher, report)
    await session.commit()
    text = await EmbeddingRepository(session).build_text(report)
    assert "Pet threat" in text
    assert "Pet loses threat after attack." in text


async def test_build_text_falls_back_to_title_when_not_enriched(session):
    await ingest_event(session, EVT)
    await session.commit()
    report = await ReportRepository(session).get_report("ips:r1842")
    text = await EmbeddingRepository(session).build_text(report)
    assert text == "Pet bug"


async def test_exists_false_then_add_then_true_and_all_with_vectors(session):
    await ingest_event(session, EVT)
    await session.commit()
    report = await ReportRepository(session).get_report("ips:r1842")
    repo = EmbeddingRepository(session)
    assert await repo.exists(report.id, "fake-embed", "h") is False
    repo.add(report_id=report.id, model="fake-embed", dimensions=3,
             input_hash="h", vector=[0.1, 0.2, 0.3])
    await session.commit()
    assert await repo.exists(report.id, "fake-embed", "h") is True
    assert await session.scalar(select(func.count()).select_from(Embedding)) == 1
    pairs = await repo.all_with_vectors("fake-embed")
    assert pairs == [(report.id, [0.1, 0.2, 0.3])]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd mai && pytest tests/test_embedding_repo.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mai.repository.embeddings'`

- [ ] **Step 4: Write `repository/embeddings.py`**

```python
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from mai.db.models import Embedding, Enrichment, Report


class EmbeddingRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def build_text(self, report: Report) -> str:
        """Embed-text: the latest enrichment's title+summary, else the report title."""
        enr = await self._session.scalar(
            select(Enrichment)
            .where(Enrichment.report_id == report.id)
            .order_by(desc(Enrichment.created_at))
            .limit(1)
        )
        if enr is not None:
            r = enr.result
            return f"{r.get('normalized_title', '')}\n{r.get('english_summary', '')}".strip()
        return report.title

    async def exists(self, report_id: str, model: str, input_hash: str) -> bool:
        return bool(await self._session.scalar(
            select(Embedding.id).where(
                Embedding.report_id == report_id,
                Embedding.model == model,
                Embedding.input_hash == input_hash,
            )
        ))

    def add(self, **kw) -> Embedding:
        row = Embedding(**kw)
        self._session.add(row)
        return row

    async def all_with_vectors(self, model: str) -> list[tuple[str, list[float]]]:
        rows = await self._session.scalars(
            select(Embedding).where(Embedding.model == model)
        )
        return [(row.report_id, row.vector) for row in rows]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd mai && pytest tests/test_embedding_repo.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add mai/src/mai/db/models.py mai/src/mai/repository/embeddings.py mai/tests/test_embedding_repo.py
git commit -m "feat: Embedding model + EmbeddingRepository (json vectors, enriched embed-text)"
```

---

### Task 3: Embedder protocol, FakeEmbedder, and orchestration

**Files:**
- Create: `mai/src/mai/embed/embedder.py` (protocol only in this task)
- Create: `mai/src/mai/embed/fake.py`
- Create: `mai/src/mai/embed_run.py`
- Create: `mai/tests/test_embed_run.py`

- [ ] **Step 1: Write `embed/embedder.py` (protocol only; HttpEmbedder added in Task 4)**

```python
from typing import Protocol


class Embedder(Protocol):
    @property
    def model(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    async def embed(self, text: str) -> list[float]: ...
```

- [ ] **Step 2: Write `embed/fake.py`**

```python
import hashlib


class FakeEmbedder:
    """Deterministic Embedder for tests: same text -> same vector. Counts calls."""

    def __init__(self, dimensions: int = 8, model: str = "fake-embed"):
        self._dim = dimensions
        self._model = model
        self.calls = 0

    @property
    def model(self) -> str:
        return self._model

    @property
    def dimensions(self) -> int:
        return self._dim

    async def embed(self, text: str) -> list[float]:
        self.calls += 1
        digest = hashlib.sha256(text.encode()).digest()
        return [digest[i % len(digest)] / 255.0 for i in range(self._dim)]
```

- [ ] **Step 3: Write the failing test**

`mai/tests/test_embed_run.py`:

```python
from sqlalchemy import func, select

from mai.contracts import IntakeEvent
from mai.db.models import Embedding
from mai.embed.fake import FakeEmbedder
from mai.embed_run import embed_pending, embed_report, most_similar
from mai.ingest import ingest_event
from mai.repository.embeddings import EmbeddingRepository
from mai.repository.reports import ReportRepository


def _evt(rid: str, title: str) -> IntakeEvent:
    return IntakeEvent("ips", rid, title, "zero", status="new",
                       raw_payload={"markdown": title})


async def test_embed_report_creates_then_caches(session):
    await ingest_event(session, _evt("r1", "Pet threat bug"))
    await session.commit()
    report = await ReportRepository(session).get_report("ips:r1")
    embedder = FakeEmbedder()
    assert await embed_report(session, embedder, report) is True
    await session.commit()
    assert await embed_report(session, embedder, report) is False
    assert embedder.calls == 1
    assert await session.scalar(select(func.count()).select_from(Embedding)) == 1


async def test_embed_pending_embeds_all_then_none(session):
    await ingest_event(session, _evt("r1", "Pet bug"))
    await ingest_event(session, _evt("r2", "Mount bug"))
    await session.commit()
    embedder = FakeEmbedder()
    assert await embed_pending(session, embedder) == 2
    assert await embed_pending(session, embedder) == 0


async def test_most_similar_ranks_and_excludes_self(session):
    for rid, title in [("r1", "alpha"), ("r2", "beta"), ("r3", "gamma")]:
        await ingest_event(session, _evt(rid, title))
    await session.commit()
    embedder = FakeEmbedder()
    await embed_pending(session, embedder)
    r1 = await ReportRepository(session).get_report("ips:r1")
    pairs = await EmbeddingRepository(session).all_with_vectors(embedder.model)
    r1_vector = next(v for rid, v in pairs if rid == r1.id)
    ranked = await most_similar(session, embedder.model, r1_vector, top_k=2,
                                exclude_report_id=r1.id)
    assert len(ranked) == 2
    assert r1.id not in [rid for rid, _ in ranked]
    assert ranked[0][1] >= ranked[1][1]  # sorted descending by score
```

- [ ] **Step 4: Run test to verify it fails**

Run: `cd mai && pytest tests/test_embed_run.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mai.embed_run'`

- [ ] **Step 5: Write `embed_run.py`**

```python
import hashlib

from sqlalchemy.ext.asyncio import AsyncSession

from mai.db.models import Report
from mai.embed.embedder import Embedder
from mai.embed.similarity import cosine
from mai.repository.embeddings import EmbeddingRepository
from mai.repository.reports import ReportRepository


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


async def embed_report(session: AsyncSession, embedder: Embedder, report: Report) -> bool:
    """Embed one report's text unless an identical embedding exists. True if created."""
    repo = EmbeddingRepository(session)
    text = await repo.build_text(report)
    input_hash = _hash(text)
    if await repo.exists(report.id, embedder.model, input_hash):
        return False
    vector = await embedder.embed(text)
    repo.add(report_id=report.id, model=embedder.model, dimensions=embedder.dimensions,
             input_hash=input_hash, vector=vector)
    return True


async def embed_pending(session: AsyncSession, embedder: Embedder) -> int:
    """Embed every report lacking a current embedding. Commits per write (resumable)."""
    reports = await ReportRepository(session).all_reports()
    count = 0
    for report in reports:
        if await embed_report(session, embedder, report):
            count += 1
            await session.commit()
    return count


async def most_similar(session: AsyncSession, model: str, query_vector: list[float],
                       top_k: int = 5, exclude_report_id: str | None = None
                       ) -> list[tuple[str, float]]:
    """Rank stored vectors of `model` by cosine similarity to query_vector."""
    pairs = await EmbeddingRepository(session).all_with_vectors(model)
    scored = [(rid, cosine(query_vector, vec))
              for rid, vec in pairs if rid != exclude_report_id]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:top_k]
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd mai && pytest tests/test_embed_run.py -v`
Expected: PASS (3 passed)

- [ ] **Step 7: Commit**

```bash
git add mai/src/mai/embed/embedder.py mai/src/mai/embed/fake.py mai/src/mai/embed_run.py mai/tests/test_embed_run.py
git commit -m "feat: Embedder protocol + FakeEmbedder + embed orchestration + most_similar"
```

---

### Task 4: HttpEmbedder (real httpx client)

**Files:**
- Modify: `mai/src/mai/embed/embedder.py` (add `HttpEmbedder`)
- Create: `mai/tests/test_http_embedder.py`

- [ ] **Step 1: Write the failing test**

`mai/tests/test_http_embedder.py`:

```python
import httpx
import pytest

from mai.embed.embedder import HttpEmbedder


def _ok(request: httpx.Request) -> httpx.Response:
    assert request.headers["Authorization"] == "Bearer emb-key"
    assert request.url.path == "/v1/embeddings"
    return httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2, 0.3]}]})


def _http_error(request: httpx.Request) -> httpx.Response:
    return httpx.Response(401, json={"error": "unauthorized"})


async def test_http_embedder_returns_vector():
    async with httpx.AsyncClient(transport=httpx.MockTransport(_ok)) as http:
        embedder = HttpEmbedder("emb-key", "text-embedding-3-small", 3, client=http)
        vector = await embedder.embed("hello")
    assert vector == [0.1, 0.2, 0.3]
    assert embedder.model == "text-embedding-3-small"
    assert embedder.dimensions == 3


async def test_http_embedder_raises_on_http_error():
    async with httpx.AsyncClient(transport=httpx.MockTransport(_http_error)) as http:
        embedder = HttpEmbedder("emb-key", "text-embedding-3-small", 3, client=http)
        with pytest.raises(httpx.HTTPStatusError):
            await embedder.embed("hello")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mai && pytest tests/test_http_embedder.py -v`
Expected: FAIL with `ImportError: cannot import name 'HttpEmbedder'`

- [ ] **Step 3: Add `HttpEmbedder` to `embed/embedder.py`**

Append to `mai/src/mai/embed/embedder.py` (keep the `Embedder` protocol; add the import at top):

```python
import httpx


class HttpEmbedder:
    """Production Embedder backed by an OpenAI-compatible /v1/embeddings endpoint."""

    def __init__(self, api_key: str, model: str, dimensions: int,
                 base_url: str = "https://api.openai.com",
                 client: httpx.AsyncClient | None = None):
        self._model = model
        self._dimensions = dimensions
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

    @property
    def dimensions(self) -> int:
        return self._dimensions

    async def embed(self, text: str) -> list[float]:
        resp = await self._client.post(
            self._base + "/v1/embeddings",
            json={"model": self._model, "input": text, "dimensions": self._dimensions},
            headers=self._headers,
        )
        resp.raise_for_status()
        return resp.json()["data"][0]["embedding"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd mai && pytest tests/test_http_embedder.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add mai/src/mai/embed/embedder.py mai/tests/test_http_embedder.py
git commit -m "feat: HttpEmbedder (OpenAI-compatible /v1/embeddings, MockTransport-tested)"
```

---

### Task 5: CLI wiring (embed) and full-suite green

**Files:**
- Modify: `mai/src/mai/config.py` (add embedding settings)
- Modify: `mai/src/mai/cli/__main__.py` (add embed subcommand)

- [ ] **Step 1: Add settings to `config.py`**

In `mai/src/mai/config.py`, add four fields to `Settings` (below the openrouter fields):

```python
    embedding_api_key: str | None = None
    embedding_api_url: str = "https://api.openai.com"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536
```

- [ ] **Step 2: Add the `embed` subcommand to `cli/__main__.py`**

Add this coroutine (after `_enrich`):

```python
async def _embed() -> int:
    if not settings.embedding_api_key:
        raise SystemExit("EMBEDDING_API_KEY not set")
    import httpx

    from mai.embed.embedder import HttpEmbedder
    from mai.embed_run import embed_pending

    async with httpx.AsyncClient(timeout=120.0) as http:
        embedder = HttpEmbedder(
            settings.embedding_api_key,
            settings.embedding_model,
            settings.embedding_dimensions,
            base_url=settings.embedding_api_url,
            client=http,
        )
        async with SessionFactory() as session:
            return await embed_pending(session, embedder)
```

Register the parser (`sub.add_parser("embed")`) and add this dispatch branch:

```python
    elif args.cmd == "embed":
        count = asyncio.run(_embed())
        print(f"embedded {count} reports")
```

- [ ] **Step 3: Run the full test suite**

Run: `cd mai && pytest -q`
Expected: PASS (55 passed — 42 prior + 13 new).

- [ ] **Step 4: Verify the `embed` subcommand is wired (guard fires without a key)**

Run: `cd mai && python -m mai.cli.__main__ embed` (with `PYTHONPATH=src` if needed, no `EMBEDDING_API_KEY` set)
Expected: exits with `EMBEDDING_API_KEY not set`.

- [ ] **Step 5: (Optional, needs a key) Live embed smoke test**

If `EMBEDDING_API_KEY` is available and reports have been ingested:
```bash
cd mai && EMBEDDING_API_KEY=<key> python -m mai.cli.__main__ embed
```
Expected: prints `embedded N reports`. If no key, SKIP and note it — orchestration is covered by `test_embed_run.py` with the fake.

- [ ] **Step 6: Commit**

```bash
git add mai/src/mai/config.py mai/src/mai/cli/__main__.py
git commit -m "feat: mai CLI embed subcommand"
```

---

## Self-Review

- **Spec coverage:** Implements the embeddings half of spec §7 stage 3 — embed the enriched English text so Plan 06 correlation can rank candidates by cosine similarity. Vectors stored JSON + Python cosine keep it offline-testable and infra-free.
- **Invariants:** derived & recomputable (Embedding never touches raw/report/enrichment) ✓ · provenance + cache key `(report_id, model, input_hash)` ✓ · store-swappable behind `EmbeddingRepository` (pgvector later) ✓ · pluggable embedder (Fake/Http behind `Embedder`) ✓ · resumable (commit per write) ✓ · embeds clean enriched text when present ✓.
- **Placeholder scan:** none — every step has runnable code/commands.
- **Type consistency:** `Embedder.model`/`dimensions` (properties) + `embed(text) -> list[float]` match `FakeEmbedder` and `HttpEmbedder`; cache key `(report_id, model, input_hash)` matches `EmbeddingRepository.exists` and `uq_embedding_key`; `most_similar` consumes `all_with_vectors` pairs and the `cosine` helper.

## Notes for later plans

- **pgvector swap (deploy plan):** replace the JSON `vector` column with a `Vector(dimensions)` column + an ANN index, and `all_with_vectors`/`most_similar` with an SQL `ORDER BY embedding <=> :q LIMIT k`. Callers (`embed_run`, Plan 06) are unaffected — the seam contains it.
- **Correlation (Plan 06):** uses `most_similar` (embedding signal) alongside explicit-reference and subsystem (`affected_entities`) signals to propose `correlation` edges + a `verification` verdict.
- **Embedding provider:** default endpoint is OpenAI (`text-embedding-3-small`, 1536-dim). `EMBEDDING_API_URL` can point at any OpenAI-compatible `/v1/embeddings` provider.
- **Re-embed on prompt/enrichment change:** because the embed text is the enriched summary, bumping the enrichment prompt changes the embed text → `input_hash` changes → re-embed automatically.
