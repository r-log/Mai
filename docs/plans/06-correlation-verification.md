# Mai Correlation & Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Link each bug report to the GitHub PR/commit/issue that likely fixes it (via explicit references + embedding similarity), then derive a verification verdict (`open` / `likely_fixed` / `fixed_confirmed`) with confidence and cited evidence — Mai's core "is this already fixed?" intelligence.

**Architecture:** Two correlators write `correlation` edges: `correlate_explicit` (regex-scans each report's raw text for `github.com/.../pull|issues/N` references and links to the matching report) and `correlate_embeddings` (cosine-ranks each bug's vector against PR vectors from Plan 05). A rule-based `verify_all` reads each bug's correlations + the related report's status and writes a `verification` verdict. Everything runs **offline over data already in the DB** — no new external API. Derived & recomputable; all DB access behind repositories.

**Tech Stack:** Python 3.12 · SQLAlchemy 2.0 async · pytest + pytest-asyncio · stdlib `re` · reuses Plan 05 `cosine`.

---

## Builds on Plans 01–05

Reuse as-is (do NOT redefine):
- `mai.db.models` — `Report`, `SourceRecord`, `ReportSourceMap`, helpers; `mai.repository.reports.ReportRepository` (`.all_reports()`, `.get_report()`).
- `mai.enrich.schema.raw_text_from_payload`; `mai.repository.embeddings.EmbeddingRepository` (`.all_with_vectors(model)`); `mai.embed.similarity.cosine`.
- `tests/conftest.py` `session` fixture; config/CLI patterns; `mai.contracts.IntakeEvent` + `mai.ingest.ingest_event` (tests).

**Design principles:** correlation/verification are **derived & recomputable** (never mutate raw/report/enrichment/embedding); idempotent upserts; repository seam; offline (no network).

## File Structure

```
src/mai/
  db/models.py                    # MODIFY: Float import + Correlation, Verification
  repository/
    reports.py                    # MODIFY: add get_by_id
    correlation.py                # CorrelationRepository + VerificationRepository
  correlate/
    __init__.py                   # new (empty)
    refs.py                       # correlate_explicit
    embedding.py                  # correlate_embeddings
    verify.py                     # verdict constants + verify_all
    run.py                        # correlate_all
  cli/__main__.py                 # MODIFY: add correlate subcommand
tests/
  test_correlation_repo.py
  test_correlate_explicit.py
  test_correlate_embedding.py
  test_verify.py
  test_correlate_run.py
```

---

### Task 1: Models + repositories

**Files:**
- Modify: `mai/src/mai/db/models.py` (Float import + two models)
- Modify: `mai/src/mai/repository/reports.py` (add `get_by_id`)
- Create: `mai/src/mai/repository/correlation.py`
- Create: `mai/tests/test_correlation_repo.py`

- [ ] **Step 1: Add `Float` to the sqlalchemy import in `db/models.py`**

Insert `Float,` into the existing `from sqlalchemy import ( ... )` block (keep all existing names).

- [ ] **Step 2: Append the two models at the end of `db/models.py`**

```python
class Correlation(Base):
    """Derived edge: report_id (a bug) is related to related_report_id (a PR/issue)."""
    __tablename__ = "correlation"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    report_id: Mapped[str] = mapped_column(ForeignKey("report.id"))
    related_report_id: Mapped[str] = mapped_column(ForeignKey("report.id"))
    method: Mapped[str] = mapped_column(String(32))   # explicit_ref | embedding
    score: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(default=_now)

    __table_args__ = (
        UniqueConstraint("report_id", "related_report_id", "method",
                         name="uq_correlation"),
    )


class Verification(Base):
    """Derived verdict for a bug report. One current row per report (upserted)."""
    __tablename__ = "verification"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    report_id: Mapped[str] = mapped_column(ForeignKey("report.id"), unique=True)
    verdict: Mapped[str] = mapped_column(String(32))   # open | likely_fixed | fixed_confirmed
    confidence: Mapped[float] = mapped_column(Float)
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(default=_now)
```

- [ ] **Step 3: Add `get_by_id` to `ReportRepository` in `repository/reports.py`**

Add this method (the file already imports `select` and `Report`):

```python
    async def get_by_id(self, report_id: str) -> "Report | None":
        return await self._session.scalar(select(Report).where(Report.id == report_id))
```

- [ ] **Step 4: Write the failing test**

`mai/tests/test_correlation_repo.py`:

```python
from mai.contracts import IntakeEvent
from mai.ingest import ingest_event
from mai.repository.correlation import CorrelationRepository, VerificationRepository
from mai.repository.reports import ReportRepository


async def _two_reports(session):
    await ingest_event(session, IntakeEvent(
        "ips", "r1", "Pet bug", "zero",
        raw_payload={"markdown": "broken; fixed in https://github.com/zero/server/pull/7"}))
    await ingest_event(session, IntakeEvent(
        "gh_pr", "zero/server#7", "Fix pet", "zero", status="merged",
        raw_payload={"body": "fixes pet threat"}))
    await session.commit()
    rr = ReportRepository(session)
    return await rr.get_report("ips:r1"), await rr.get_report("gh_pr:zero/server#7")


async def test_report_text_and_find_by_key(session):
    bug, pr = await _two_reports(session)
    repo = CorrelationRepository(session)
    assert "github.com/zero/server/pull/7" in await repo.report_text(bug)
    found = await repo.find_report_by_key("gh_pr:zero/server#7")
    assert found is not None and found.id == pr.id


async def test_correlation_upsert_is_idempotent(session):
    bug, pr = await _two_reports(session)
    repo = CorrelationRepository(session)
    await repo.upsert(bug.id, pr.id, "explicit_ref", 1.0)
    await repo.upsert(bug.id, pr.id, "explicit_ref", 0.9)  # same key -> update, no dup
    await session.commit()
    edges = await repo.for_report(bug.id)
    assert len(edges) == 1
    assert edges[0].score == 0.9


async def test_verification_upsert_keeps_one_row(session):
    bug, _ = await _two_reports(session)
    vrepo = VerificationRepository(session)
    await vrepo.upsert(bug.id, "open", 0.1, [])
    await vrepo.upsert(bug.id, "fixed_confirmed", 0.95, [{"x": 1}])
    await session.commit()
    v = await vrepo.get(bug.id)
    assert v.verdict == "fixed_confirmed"
    assert v.confidence == 0.95
    assert await ReportRepository(session).get_by_id(bug.id) is not None
```

- [ ] **Step 5: Run test to verify it fails**

Run: `cd mai && pytest tests/test_correlation_repo.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mai.repository.correlation'`

- [ ] **Step 6: Write `repository/correlation.py`**

```python
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from mai.db.models import (
    Correlation, Report, ReportSourceMap, SourceRecord, Verification,
)
from mai.enrich.schema import raw_text_from_payload


class CorrelationRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def report_text(self, report: Report) -> str:
        """Latest raw text of the report's first mapped source."""
        maps = list(await self._session.scalars(
            select(ReportSourceMap).where(ReportSourceMap.report_id == report.id)
        ))
        for m in maps:
            rec = await self._session.scalar(
                select(SourceRecord)
                .where(SourceRecord.source_type == m.source_type,
                       SourceRecord.source_id == m.source_id)
                .order_by(desc(SourceRecord.version))
                .limit(1)
            )
            if rec is not None:
                return raw_text_from_payload(rec.source_type, rec.payload)
        return ""

    async def find_report_by_key(self, canonical_key: str) -> Report | None:
        return await self._session.scalar(
            select(Report).where(Report.canonical_key == canonical_key)
        )

    async def upsert(self, report_id: str, related_report_id: str,
                     method: str, score: float) -> None:
        existing = await self._session.scalar(
            select(Correlation).where(
                Correlation.report_id == report_id,
                Correlation.related_report_id == related_report_id,
                Correlation.method == method,
            )
        )
        if existing:
            existing.score = score
        else:
            self._session.add(Correlation(
                report_id=report_id, related_report_id=related_report_id,
                method=method, score=score))

    async def for_report(self, report_id: str) -> list[Correlation]:
        return list(await self._session.scalars(
            select(Correlation).where(Correlation.report_id == report_id)
        ))


class VerificationRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def upsert(self, report_id: str, verdict: str, confidence: float,
                     evidence: list) -> None:
        existing = await self._session.scalar(
            select(Verification).where(Verification.report_id == report_id)
        )
        if existing:
            existing.verdict = verdict
            existing.confidence = confidence
            existing.evidence = evidence
        else:
            self._session.add(Verification(
                report_id=report_id, verdict=verdict,
                confidence=confidence, evidence=evidence))

    async def get(self, report_id: str) -> Verification | None:
        return await self._session.scalar(
            select(Verification).where(Verification.report_id == report_id)
        )
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `cd mai && pytest tests/test_correlation_repo.py -v`
Expected: PASS (3 passed)

- [ ] **Step 8: Commit**

```bash
git add mai/src/mai/db/models.py mai/src/mai/repository/reports.py mai/src/mai/repository/correlation.py mai/tests/test_correlation_repo.py
git commit -m "feat: Correlation/Verification models + repositories"
```

---

### Task 2: Explicit-reference correlator

**Files:**
- Create: `mai/src/mai/correlate/__init__.py`
- Create: `mai/src/mai/correlate/refs.py`
- Create: `mai/tests/test_correlate_explicit.py`

- [ ] **Step 1: Write the failing test**

`mai/tests/test_correlate_explicit.py`:

```python
from mai.contracts import IntakeEvent
from mai.correlate.refs import correlate_explicit
from mai.ingest import ingest_event
from mai.repository.correlation import CorrelationRepository
from mai.repository.reports import ReportRepository


async def test_correlate_explicit_links_ips_bug_to_referenced_pr(session):
    await ingest_event(session, IntakeEvent(
        "ips", "r1", "Pet bug", "zero",
        raw_payload={"markdown": "Looks fixed by https://github.com/zero/server/pull/7 now"}))
    await ingest_event(session, IntakeEvent(
        "gh_pr", "zero/server#7", "Fix pet", "zero", status="merged",
        raw_payload={"body": "x"}))
    await session.commit()
    n = await correlate_explicit(session)
    await session.commit()
    assert n == 1
    rr = ReportRepository(session)
    bug = await rr.get_report("ips:r1")
    pr = await rr.get_report("gh_pr:zero/server#7")
    edges = await CorrelationRepository(session).for_report(bug.id)
    assert len(edges) == 1
    assert edges[0].related_report_id == pr.id
    assert edges[0].method == "explicit_ref"
    assert edges[0].score == 1.0


async def test_correlate_explicit_ignores_refs_to_unknown_reports(session):
    await ingest_event(session, IntakeEvent(
        "ips", "r2", "Bug", "zero",
        raw_payload={"markdown": "see https://github.com/zero/server/pull/999"}))
    await session.commit()
    assert await correlate_explicit(session) == 0  # PR #999 not in our DB
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mai && pytest tests/test_correlate_explicit.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mai.correlate'`

- [ ] **Step 3: Create `correlate/__init__.py` (empty marker)**

```python
```

- [ ] **Step 4: Write `correlate/refs.py`**

```python
import re

from sqlalchemy.ext.asyncio import AsyncSession

from mai.repository.correlation import CorrelationRepository
from mai.repository.reports import ReportRepository

_REF_RE = re.compile(
    r"github\.com/([\w.-]+/[\w.-]+)/(pull|issues)/(\d+)", re.IGNORECASE)
_KIND = {"pull": "gh_pr", "issues": "gh_issue"}


async def correlate_explicit(session: AsyncSession) -> int:
    """Link reports to GitHub PRs/issues they textually reference (if we have them)."""
    crepo = CorrelationRepository(session)
    reports = await ReportRepository(session).all_reports()
    edges = 0
    for report in reports:
        text = await crepo.report_text(report)
        for full_name, kind, num in _REF_RE.findall(text):
            key = f"{_KIND[kind.lower()]}:{full_name}#{num}"
            related = await crepo.find_report_by_key(key)
            if related is not None and related.id != report.id:
                await crepo.upsert(report.id, related.id, "explicit_ref", 1.0)
                edges += 1
    return edges
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd mai && pytest tests/test_correlate_explicit.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add mai/src/mai/correlate/__init__.py mai/src/mai/correlate/refs.py mai/tests/test_correlate_explicit.py
git commit -m "feat: explicit-reference correlator (github pull/issue refs -> edges)"
```

---

### Task 3: Embedding-similarity correlator

**Files:**
- Create: `mai/src/mai/correlate/embedding.py`
- Create: `mai/tests/test_correlate_embedding.py`

- [ ] **Step 1: Write the failing test**

`mai/tests/test_correlate_embedding.py`:

```python
from mai.contracts import IntakeEvent
from mai.correlate.embedding import correlate_embeddings
from mai.embed.fake import FakeEmbedder
from mai.embed_run import embed_pending
from mai.ingest import ingest_event
from mai.repository.correlation import CorrelationRepository
from mai.repository.reports import ReportRepository


async def test_correlate_embeddings_links_bug_to_pr_candidates(session):
    await ingest_event(session, IntakeEvent("ips", "r1", "pet threat broken", "zero",
                                            raw_payload={"markdown": "pet threat broken"}))
    await ingest_event(session, IntakeEvent("gh_pr", "zero/server#7", "fix pet threat",
                                            "zero", status="merged",
                                            raw_payload={"body": "fix"}))
    await session.commit()
    embedder = FakeEmbedder()
    await embed_pending(session, embedder)
    await session.commit()
    # threshold 0.0 so the single PR candidate is always linked (mechanics test)
    n = await correlate_embeddings(session, embedder.model, top_k=3, threshold=0.0)
    await session.commit()
    assert n == 1
    rr = ReportRepository(session)
    bug = await rr.get_report("ips:r1")
    pr = await rr.get_report("gh_pr:zero/server#7")
    edges = await CorrelationRepository(session).for_report(bug.id)
    assert [(e.related_report_id, e.method) for e in edges] == [(pr.id, "embedding")]
    assert 0.0 <= edges[0].score <= 1.0


async def test_correlate_embeddings_does_not_link_bug_to_itself(session):
    await ingest_event(session, IntakeEvent("ips", "r1", "only a bug", "zero",
                                            raw_payload={"markdown": "only a bug"}))
    await session.commit()
    await embed_pending(session, FakeEmbedder())
    await session.commit()
    # no gh_pr targets -> no edges
    assert await correlate_embeddings(session, "fake-embed", top_k=3, threshold=0.0) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mai && pytest tests/test_correlate_embedding.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mai.correlate.embedding'`

- [ ] **Step 3: Write `correlate/embedding.py`**

```python
from sqlalchemy.ext.asyncio import AsyncSession

from mai.embed.similarity import cosine
from mai.repository.correlation import CorrelationRepository
from mai.repository.embeddings import EmbeddingRepository
from mai.repository.reports import ReportRepository


async def correlate_embeddings(session: AsyncSession, model: str,
                               source_prefix: str = "ips:",
                               target_prefix: str = "gh_pr:",
                               top_k: int = 3, threshold: float = 0.5) -> int:
    """Link each source-report's vector to its top_k most similar target vectors."""
    pairs = await EmbeddingRepository(session).all_with_vectors(model)
    reports = {r.id: r for r in await ReportRepository(session).all_reports()}
    sources = [(rid, v) for rid, v in pairs
               if rid in reports and reports[rid].canonical_key.startswith(source_prefix)]
    targets = [(rid, v) for rid, v in pairs
               if rid in reports and reports[rid].canonical_key.startswith(target_prefix)]
    crepo = CorrelationRepository(session)
    edges = 0
    for srid, svec in sources:
        scored = sorted(
            ((trid, cosine(svec, tvec)) for trid, tvec in targets if trid != srid),
            key=lambda pair: pair[1], reverse=True,
        )[:top_k]
        for trid, score in scored:
            if score >= threshold:
                await crepo.upsert(srid, trid, "embedding", score)
                edges += 1
    return edges
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd mai && pytest tests/test_correlate_embedding.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add mai/src/mai/correlate/embedding.py mai/tests/test_correlate_embedding.py
git commit -m "feat: embedding-similarity correlator (bug -> top-k PR candidates)"
```

---

### Task 4: Verification engine

**Files:**
- Create: `mai/src/mai/correlate/verify.py`
- Create: `mai/tests/test_verify.py`

- [ ] **Step 1: Write the failing test**

`mai/tests/test_verify.py`:

```python
from mai.contracts import IntakeEvent
from mai.correlate.verify import (
    VERDICT_CONFIRMED, VERDICT_LIKELY, VERDICT_OPEN, verify_all,
)
from mai.ingest import ingest_event
from mai.repository.correlation import CorrelationRepository, VerificationRepository
from mai.repository.reports import ReportRepository


async def _bug_and_pr(session, pr_status):
    await ingest_event(session, IntakeEvent("ips", "r1", "Bug", "zero",
                                            raw_payload={"markdown": "x"}))
    await ingest_event(session, IntakeEvent("gh_pr", "zero/server#7", "Fix", "zero",
                                            status=pr_status, raw_payload={"body": "y"}))
    await session.commit()
    rr = ReportRepository(session)
    return await rr.get_report("ips:r1"), await rr.get_report("gh_pr:zero/server#7")


async def test_explicit_ref_to_merged_pr_is_confirmed(session):
    bug, pr = await _bug_and_pr(session, "merged")
    await CorrelationRepository(session).upsert(bug.id, pr.id, "explicit_ref", 1.0)
    await session.commit()
    await verify_all(session)
    await session.commit()
    v = await VerificationRepository(session).get(bug.id)
    assert v.verdict == VERDICT_CONFIRMED
    assert v.confidence == 0.95
    assert v.evidence[0]["related"] == "gh_pr:zero/server#7"


async def test_explicit_ref_to_open_pr_is_likely(session):
    bug, pr = await _bug_and_pr(session, "open")
    await CorrelationRepository(session).upsert(bug.id, pr.id, "explicit_ref", 1.0)
    await session.commit()
    await verify_all(session)
    await session.commit()
    v = await VerificationRepository(session).get(bug.id)
    assert v.verdict == VERDICT_LIKELY


async def test_no_correlations_is_open(session):
    bug, _ = await _bug_and_pr(session, "merged")
    await verify_all(session)
    await session.commit()
    v = await VerificationRepository(session).get(bug.id)
    assert v.verdict == VERDICT_OPEN
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mai && pytest tests/test_verify.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mai.correlate.verify'`

- [ ] **Step 3: Write `correlate/verify.py`**

```python
from sqlalchemy.ext.asyncio import AsyncSession

from mai.db.models import Report
from mai.repository.correlation import CorrelationRepository, VerificationRepository
from mai.repository.reports import ReportRepository

VERDICT_OPEN = "open"
VERDICT_LIKELY = "likely_fixed"
VERDICT_CONFIRMED = "fixed_confirmed"

_BUG_PREFIXES = ("ips:", "gh_issue:")


async def verify_report(session: AsyncSession, report: Report) -> str:
    crepo = CorrelationRepository(session)
    rrepo = ReportRepository(session)
    corrs = await crepo.for_report(report.id)
    verdict, confidence, evidence = VERDICT_OPEN, 0.1, []
    for c in corrs:
        related = await rrepo.get_by_id(c.related_report_id)
        merged = related is not None and related.status == "merged"
        evidence.append({
            "related": related.canonical_key if related else c.related_report_id,
            "method": c.method, "score": c.score, "merged": merged,
        })
        if c.method == "explicit_ref" and merged:
            verdict, confidence = VERDICT_CONFIRMED, max(confidence, 0.95)
        elif c.method == "explicit_ref":
            if verdict != VERDICT_CONFIRMED:
                verdict = VERDICT_LIKELY
            confidence = max(confidence, 0.7)
        elif c.method == "embedding" and merged and c.score >= 0.7:
            if verdict == VERDICT_OPEN:
                verdict = VERDICT_LIKELY
            confidence = max(confidence, c.score)
    await VerificationRepository(session).upsert(report.id, verdict, confidence, evidence)
    return verdict


async def verify_all(session: AsyncSession) -> int:
    reports = await ReportRepository(session).all_reports()
    count = 0
    for report in reports:
        if report.canonical_key.startswith(_BUG_PREFIXES):
            await verify_report(session, report)
            count += 1
    return count
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd mai && pytest tests/test_verify.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add mai/src/mai/correlate/verify.py mai/tests/test_verify.py
git commit -m "feat: rule-based verification verdict (open/likely_fixed/fixed_confirmed)"
```

---

### Task 5: Orchestration + CLI

**Files:**
- Create: `mai/src/mai/correlate/run.py`
- Create: `mai/tests/test_correlate_run.py`
- Modify: `mai/src/mai/cli/__main__.py` (add correlate subcommand)

- [ ] **Step 1: Write the failing test**

`mai/tests/test_correlate_run.py`:

```python
from mai.contracts import IntakeEvent
from mai.correlate.run import correlate_all
from mai.embed.fake import FakeEmbedder
from mai.embed_run import embed_pending
from mai.ingest import ingest_event
from mai.repository.correlation import VerificationRepository
from mai.repository.reports import ReportRepository


async def test_correlate_all_links_and_verifies(session):
    await ingest_event(session, IntakeEvent(
        "ips", "r1", "pet threat broken", "zero",
        raw_payload={"markdown": "fixed by https://github.com/zero/server/pull/7"}))
    await ingest_event(session, IntakeEvent(
        "gh_pr", "zero/server#7", "fix pet threat", "zero", status="merged",
        raw_payload={"body": "fix"}))
    await session.commit()
    embedder = FakeEmbedder()
    await embed_pending(session, embedder)
    await session.commit()
    result = await correlate_all(session, embedder.model, threshold=0.0)
    assert result["explicit_edges"] == 1
    assert result["embedding_edges"] == 1
    assert result["verified"] == 1
    bug = await ReportRepository(session).get_report("ips:r1")
    v = await VerificationRepository(session).get(bug.id)
    assert v.verdict == "fixed_confirmed"  # explicit ref to a merged PR
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mai && pytest tests/test_correlate_run.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mai.correlate.run'`

- [ ] **Step 3: Write `correlate/run.py`**

```python
from sqlalchemy.ext.asyncio import AsyncSession

from mai.correlate.embedding import correlate_embeddings
from mai.correlate.refs import correlate_explicit
from mai.correlate.verify import verify_all


async def correlate_all(session: AsyncSession, model: str,
                        threshold: float = 0.5) -> dict:
    """Run both correlators then verification over everything in the DB. Offline."""
    explicit_edges = await correlate_explicit(session)
    embedding_edges = await correlate_embeddings(session, model, threshold=threshold)
    verified = await verify_all(session)
    await session.commit()
    return {"explicit_edges": explicit_edges,
            "embedding_edges": embedding_edges,
            "verified": verified}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd mai && pytest tests/test_correlate_run.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Add the `correlate` subcommand to `cli/__main__.py`**

Add this coroutine (after `_embed`):

```python
async def _correlate() -> dict:
    from mai.correlate.run import correlate_all

    async with SessionFactory() as session:
        return await correlate_all(session, settings.embedding_model)
```

Register the parser (`sub.add_parser("correlate")`) and add this dispatch branch:

```python
    elif args.cmd == "correlate":
        result = asyncio.run(_correlate())
        print(f"correlate: explicit={result['explicit_edges']} "
              f"embedding={result['embedding_edges']} verified={result['verified']}")
```

- [ ] **Step 6: Run the full test suite**

Run: `cd mai && pytest -q`
Expected: PASS (65 passed — 54 prior + 11 new).

- [ ] **Step 7: Verify the `correlate` subcommand runs offline (no key needed)**

Run: `cd mai && python -m mai.cli.__main__ init-db && python -m mai.cli.__main__ correlate` (PYTHONPATH=src if needed)
Expected: prints `correlate: explicit=0 embedding=0 verified=0` on an empty DB (no key required — it's offline).

- [ ] **Step 8: Commit**

```bash
git add mai/src/mai/correlate/run.py mai/tests/test_correlate_run.py mai/src/mai/cli/__main__.py
git commit -m "feat: correlate_all orchestration + mai CLI correlate subcommand"
```

---

## Self-Review

- **Spec coverage:** Implements spec §6 `correlation` + `verification` and the correlation engine of §7 stage 3 — explicit-reference + embedding signals → verdict with cited evidence. This is the "is it already fixed?" intelligence that is Mai's core value (spec §1).
- **Invariants:** derived & recomputable (never mutates raw/report/enrichment/embedding) ✓ · idempotent upserts (`uq_correlation`, unique `verification.report_id`) ✓ · repository seam (`CorrelationRepository`/`VerificationRepository`) ✓ · offline (no external API) ✓ · evidence cited per verdict ✓.
- **Placeholder scan:** none — every step has runnable code/commands.
- **Type consistency:** `CorrelationRepository.upsert(report_id, related_report_id, method, score)` / `for_report` used identically by both correlators and `verify`; verdict constants (`VERDICT_*`) shared; `correlate_all` returns the keys the CLI prints.

## Notes for later plans

- **LLM judge (future):** the rule-based verdict can be augmented by an adversarial LLM check ("does this PR actually fix this bug?") behind a `Verifier` protocol, gated like enrichment — higher precision before any write-back.
- **Commit refs (with 02b):** once commit reports exist, extend `_REF_RE` to `commit/<sha>` and treat a merged-commit reference as confirming.
- **Subsystem signal:** add a third correlator using enrichment `affected_entities` vs PR touched-files for a non-embedding semantic signal.
- **Publish (later):** surface each bug's verdict + evidence in the Hugo `.md` so the dashboard shows "likely fixed by PR #7".
- **Threshold tuning:** `correlate_all` uses the default embedding threshold (0.5); tune against real data once embeddings are populated.
