# Review Advisor P2 — Judge + Guardrail Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a grounded, advisory LLM opinion to each REVIEW-lane item — a schema-validated `ReviewOpinion` whose every claim is verified against the deterministic P1 evidence before display — exposed through the existing `GET /api/review/{item_id}` and rendered beside the evidence panel.

**Architecture:** A new `src/mai/judge/` package mirrors the proven `src/mai/enrich/` seam: a `ReviewJudge` Protocol + `OpenRouterJudge` (httpx, strict JSON, temp 0) + `FakeJudge`, a `ReviewOpinion` pydantic schema, a content-aware `choose_model` router (Claude Sonnet by default, Gemini Pro for many-hunk/large-context fixes), and `ground_opinion` — the truthfulness lock that drops any tip/citation/adapted-hunk not referencing a path/line/sha present in the evidence and discounts confidence by the grounded fraction. `build_review_advice` orchestrates collect → judge → ground; the API and board render `{evidence, opinion}`. Computed on-demand, no persistence (cache table is P3).

**Tech Stack:** Python 3.12, async, httpx, pydantic, FastAPI/Starlette, pytest (asyncio_mode=auto). No new dependencies — httpx and pydantic are already present.

## Global Constraints

- **Invariant 1 — NEEDS is never LLM-touched.** The judge runs ONLY for `verdict == "review"`. `build_review_evidence` already returns `None` for non-review items; when it does, `build_review_advice` returns `opinion=None` and never calls the judge.
- **Invariant 2 — Grounded-only.** Every tip/citation/adapted-hunk the user sees must reference a path / `path:line` / sha present in the collected evidence; `ground_opinion` removes the rest. The model cannot opine on unseen code.
- **Advisory, not deciding.** The opinion never changes a verdict, never claims/ports/resolves. It is a labeled assessment + grounded confidence + tips.
- **Offline except the single LLM call.** Evidence is local git/DB; only `OpenRouterJudge` makes a network call. No key / `review_advisor_enabled=false` ⇒ `opinion=None`, endpoint still 200.
- **Zero retry** on a malformed/empty model response — catch `ReviewOpinionSchemaError`, return `opinion=None` (panel degrades to evidence-only).
- **Mirror the `enrich/` seam exactly** (Protocol + OpenRouter impl + Fake; `response_format={"type":"json_object"}`; `temperature=0`; a 200 with non-JSON/empty content raises the schema error).
- **No AI attribution** in commits (no `Co-Authored-By`, no "Generated with", no emoji). Conventional-commit style (`feat:`/`test:`/`docs:`).
- **4-space indent** (Python); match the neighbouring file's style. JS matches portboard.js's existing indentation.
- Design source of truth: `docs/specs/review-advisor-p2-design.md` (and master `docs/specs/review-advisor.md`).

---

## File Structure

| File | Change | Responsibility |
|------|--------|----------------|
| `src/mai/judge/__init__.py` | Create | empty package marker |
| `src/mai/judge/schema.py` | Create | `ReviewOpinion`/`AdaptedHunk` pydantic + `parse_opinion` + `ReviewOpinionSchemaError` |
| `src/mai/judge/ground.py` | Create | `ground_opinion(opinion, evidence)` — the truthfulness lock |
| `src/mai/judge/prompt.py` | Create | `SYSTEM_PROMPT` + `build_prompt(evidence)` + `PROMPT_VERSION` |
| `src/mai/judge/judge.py` | Create | `ReviewJudge` Protocol + `OpenRouterJudge` + `choose_model(evidence, settings)` |
| `src/mai/judge/fake.py` | Create | `FakeJudge` test double (records `calls`, `last_model`) |
| `src/mai/sync/review.py` | Modify | add `build_review_advice(session, git_client, judge, item_id)` |
| `src/mai/config.py` | Modify | `review_advisor_enabled`, `review_model`, `review_model_large`, thresholds |
| `src/mai/web/review_api.py` | Modify | route returns `{evidence, opinion}`; build judge from settings; `judge=` inject seam |
| `src/mai/web/app.py` | Modify | `create_app(review_judge=None)` → pass to `make_review_router` |
| `src/mai/web/static/portboard.js` | Modify | render the opinion below the evidence |
| `src/mai/web/static/board.css` | Modify | `.rev-op*` styles |
| `tests/test_review_opinion_schema.py` | Create | schema parse/validation |
| `tests/test_ground_opinion.py` | Create | the grounding guardrail (headline) |
| `tests/test_review_judge.py` | Create | `choose_model` router + `OpenRouterJudge` mocked-httpx + `FakeJudge` |
| `tests/test_review_advice.py` | Create | `build_review_advice` orchestration + Invariant 1 |
| `tests/test_review_api.py` | Modify | endpoint returns `{evidence, opinion}` with an injected `FakeJudge` |

---

### Task 1: `ReviewOpinion` schema

**Files:**
- Create: `src/mai/judge/__init__.py`, `src/mai/judge/schema.py`
- Test: `tests/test_review_opinion_schema.py`

**Interfaces:**
- Produces: `ReviewOpinion` (pydantic: `assessment: Literal["portable","already_handled","divergent","uncertain"]`, `confidence: float 0..1`, `reason: str`, `tips: list[str]`, `adapted_hunks: list[AdaptedHunk]`, `citations: list[str]`); `AdaptedHunk(path: str, suggestion: str)`; `parse_opinion(content: str|dict) -> ReviewOpinion`; `ReviewOpinionSchemaError(ValueError)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_review_opinion_schema.py
import pytest
from mai.judge.schema import ReviewOpinion, parse_opinion, ReviewOpinionSchemaError


def test_parse_opinion_validates_a_good_object():
    op = parse_opinion('{"assessment":"portable","confidence":0.7,"reason":"clean",'
                        '"tips":["adapt Close() in src/x.cpp"],"citations":["src/x.cpp"]}')
    assert isinstance(op, ReviewOpinion)
    assert op.assessment == "portable"
    assert op.confidence == 0.7
    assert op.tips == ["adapt Close() in src/x.cpp"]
    assert op.adapted_hunks == []          # defaults to empty


def test_parse_opinion_rejects_bad_enum():
    with pytest.raises(ReviewOpinionSchemaError):
        parse_opinion('{"assessment":"maybe","confidence":0.5,"reason":"x"}')


def test_parse_opinion_rejects_out_of_range_confidence():
    with pytest.raises(ReviewOpinionSchemaError):
        parse_opinion('{"assessment":"portable","confidence":2.0,"reason":"x"}')


def test_parse_opinion_rejects_invalid_json():
    with pytest.raises(ReviewOpinionSchemaError):
        parse_opinion("{not json")
```

- [ ] **Step 2: Run it, expect failure** — `python -m pytest tests/test_review_opinion_schema.py -q` → FAIL (module missing).

- [ ] **Step 3: Implement `src/mai/judge/__init__.py`** (empty file) and `src/mai/judge/schema.py`:

```python
# src/mai/judge/schema.py
import json
from typing import Literal

from pydantic import BaseModel, Field, ValidationError


class ReviewOpinionSchemaError(ValueError):
    """Raised when a model's output is not valid against ReviewOpinion."""


class AdaptedHunk(BaseModel):
    path: str
    suggestion: str


class ReviewOpinion(BaseModel):
    assessment: Literal["portable", "already_handled", "divergent", "uncertain"]
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    tips: list[str] = Field(default_factory=list)
    adapted_hunks: list[AdaptedHunk] = Field(default_factory=list)
    citations: list[str] = Field(default_factory=list)


def parse_opinion(content: str | dict) -> ReviewOpinion:
    """Validate raw model output (JSON string or dict) into ReviewOpinion."""
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ReviewOpinionSchemaError(f"invalid JSON: {exc}") from exc
    try:
        return ReviewOpinion.model_validate(content)
    except ValidationError as exc:
        raise ReviewOpinionSchemaError(str(exc)) from exc
```

- [ ] **Step 4: Run tests, expect pass** — `python -m pytest tests/test_review_opinion_schema.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mai/judge/__init__.py src/mai/judge/schema.py tests/test_review_opinion_schema.py
git commit -m "feat: ReviewOpinion strict schema for the review judge"
```

---

### Task 2: `ground_opinion` guardrail

**Files:**
- Create: `src/mai/judge/ground.py`
- Test: `tests/test_ground_opinion.py`

**Interfaces:**
- Consumes: `ReviewOpinion` (Task 1); the P1 evidence dict (`fix.source_sha`, `conflict.hunks[].path`/`.target_line`, `similar[].sha`).
- Produces: `ground_opinion(opinion: ReviewOpinion, evidence: dict) -> ReviewOpinion`.

Grounding rule: collect evidence tokens = every hunk `path`, every `path:target_line`, every similar/source sha (full + 10-char). A `tip`/`citation` survives only if it *contains* one of those tokens as a substring; an `adapted_hunk` survives only if its `path` is an evidence path. `confidence = llm_confidence * grounded_fraction` (fraction = kept claims / total claims; `1.0` if there were no claims). If there were claims and ALL were dropped → force `assessment="uncertain"`, `confidence=0.0`, append a manual-review note.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ground_opinion.py
from mai.judge.schema import ReviewOpinion, AdaptedHunk
from mai.judge.ground import ground_opinion

EVIDENCE = {
    "fix": {"source_sha": "abc123def456"},
    "conflict": {"hunks": [{"path": "src/x.cpp", "target_line": 65,
                            "patch_text": "@@", "target_context": "code"}]},
    "similar": [{"sha": "deadbeef0099"}],
}


def test_grounded_claim_survives_with_proportional_confidence():
    op = ReviewOpinion(assessment="portable", confidence=0.8, reason="ok",
                       tips=["rename in src/x.cpp"],          # grounded (path)
                       citations=["src/x.cpp:65", "totally/unseen.cpp"],  # 1 grounded, 1 not
                       adapted_hunks=[AdaptedHunk(path="src/x.cpp", suggestion="use Close()")])
    out = ground_opinion(op, EVIDENCE)
    assert "rename in src/x.cpp" in out.tips
    assert out.citations == ["src/x.cpp:65"]                 # ungrounded citation dropped
    assert len(out.adapted_hunks) == 1
    # 3 kept of 4 total -> 0.8 * 0.75 = 0.6
    assert out.confidence == 0.6
    assert out.assessment == "portable"


def test_all_ungrounded_forces_uncertain_zero():
    op = ReviewOpinion(assessment="portable", confidence=0.9, reason="looks fine",
                       tips=["edit some/other.cpp"], citations=["nope.cpp"])
    out = ground_opinion(op, EVIDENCE)
    assert out.assessment == "uncertain"
    assert out.confidence == 0.0
    assert out.tips == [] and out.citations == []
    assert "ungrounded" in out.reason.lower()


def test_no_claims_keeps_confidence_unchanged():
    op = ReviewOpinion(assessment="divergent", confidence=0.5, reason="differs")
    out = ground_opinion(op, EVIDENCE)
    assert out.assessment == "divergent"
    assert out.confidence == 0.5            # nothing to verify -> fraction 1.0
```

- [ ] **Step 2: Run it, expect failure** — `python -m pytest tests/test_ground_opinion.py -q` → FAIL (module missing).

- [ ] **Step 3: Implement `src/mai/judge/ground.py`:**

```python
# src/mai/judge/ground.py
from mai.judge.schema import ReviewOpinion


def _evidence_tokens(evidence: dict) -> set[str]:
    toks: set[str] = set()
    sha = ((evidence.get("fix") or {}).get("source_sha"))
    if sha:
        toks.add(sha)
        toks.add(sha[:10])
    for h in (evidence.get("conflict") or {}).get("hunks") or []:
        path = h.get("path")
        if path:
            toks.add(path)
            tline = h.get("target_line")
            if tline is not None:
                toks.add(f"{path}:{tline}")
    for s in evidence.get("similar") or []:
        ssha = s.get("sha")
        if ssha:
            toks.add(ssha)
            toks.add(ssha[:10])
    return toks


def _cites(text: str, tokens: set[str]) -> bool:
    return any(tok and tok in text for tok in tokens)


def ground_opinion(opinion: ReviewOpinion, evidence: dict) -> ReviewOpinion:
    """Drop every claim not grounded in the evidence; discount confidence by the
    grounded fraction. All-ungrounded -> uncertain/0 with a manual-review note."""
    tokens = _evidence_tokens(evidence)
    kept_tips = [t for t in opinion.tips if _cites(t, tokens)]
    kept_cites = [c for c in opinion.citations if _cites(c, tokens)]
    kept_hunks = [h for h in opinion.adapted_hunks if h.path in tokens]
    total = len(opinion.tips) + len(opinion.citations) + len(opinion.adapted_hunks)
    kept = len(kept_tips) + len(kept_cites) + len(kept_hunks)

    if total > 0 and kept == 0:
        return opinion.model_copy(update={
            "assessment": "uncertain",
            "confidence": 0.0,
            "reason": opinion.reason + " [model output ungrounded — manual review]",
            "tips": [], "citations": [], "adapted_hunks": [],
        })

    fraction = 1.0 if total == 0 else kept / total
    return opinion.model_copy(update={
        "confidence": round(opinion.confidence * fraction, 3),
        "tips": kept_tips, "citations": kept_cites, "adapted_hunks": kept_hunks,
    })
```

- [ ] **Step 4: Run tests, expect pass** — `python -m pytest tests/test_ground_opinion.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mai/judge/ground.py tests/test_ground_opinion.py
git commit -m "feat: ground_opinion guardrail — drop ungrounded claims, discount confidence"
```

---

### Task 3: Judge seam + content-aware model router

**Files:**
- Create: `src/mai/judge/prompt.py`, `src/mai/judge/judge.py`, `src/mai/judge/fake.py`
- Test: `tests/test_review_judge.py`

**Interfaces:**
- Consumes: `ReviewOpinion`/`parse_opinion`/`ReviewOpinionSchemaError` (Task 1); the evidence dict.
- Produces:
  - `ReviewJudge` Protocol: `async judge(self, evidence: dict, model: str) -> ReviewOpinion`.
  - `OpenRouterJudge(api_key, base_url="https://openrouter.ai/api", client=None)`.
  - `choose_model(evidence: dict, settings) -> str`.
  - `FakeJudge(opinion=None, raises=None)` with `.calls`, `.last_model`.
  - `SYSTEM_PROMPT`, `build_prompt(evidence) -> str`, `PROMPT_VERSION`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_review_judge.py
import httpx
import pytest

from types import SimpleNamespace
from mai.judge.judge import OpenRouterJudge, choose_model
from mai.judge.fake import FakeJudge
from mai.judge.schema import ReviewOpinion, ReviewOpinionSchemaError

GOOD = ('{"assessment":"portable","confidence":0.7,"reason":"clean apply",'
        '"tips":["adapt in src/x.cpp"],"citations":["src/x.cpp"]}')

SETTINGS = SimpleNamespace(review_model="anthropic/sonnet",
                           review_model_large="google/gemini",
                           review_hunk_routing_threshold=8,
                           review_large_context_chars=24000)


def _small():
    return {"conflict": {"total": 2, "hunks": [{"patch_text": "x", "target_context": "y"}]}}


def _many_hunks():
    return {"conflict": {"total": 20, "hunks": [{"patch_text": "x", "target_context": ""}]}}


def _huge_context():
    return {"conflict": {"total": 1,
                         "hunks": [{"patch_text": "a" * 30000, "target_context": ""}]}}


def test_choose_model_small_picks_default():
    assert choose_model(_small(), SETTINGS) == "anthropic/sonnet"


def test_choose_model_many_hunks_picks_large():
    assert choose_model(_many_hunks(), SETTINGS) == "google/gemini"


def test_choose_model_huge_context_picks_large():
    assert choose_model(_huge_context(), SETTINGS) == "google/gemini"


def _ok(request: httpx.Request) -> httpx.Response:
    assert request.headers["Authorization"] == "Bearer or-key"
    assert request.url.path == "/api/v1/chat/completions"
    body = request.read().decode()
    assert '"model":"some/model"' in body.replace(" ", "")
    assert '"temperature":0' in body.replace(" ", "")
    return httpx.Response(200, json={"choices": [{"message": {"content": GOOD}}]})


def _bad(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": "{nope"}}]})


def _non_json(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, text="gateway error")


async def test_openrouter_judge_returns_validated_opinion():
    async with httpx.AsyncClient(transport=httpx.MockTransport(_ok)) as http:
        judge = OpenRouterJudge("or-key", client=http)
        op = await judge.judge({"fix": {}, "conflict": {"hunks": []}}, "some/model")
    assert isinstance(op, ReviewOpinion)
    assert op.assessment == "portable"


async def test_openrouter_judge_raises_on_bad_json():
    async with httpx.AsyncClient(transport=httpx.MockTransport(_bad)) as http:
        judge = OpenRouterJudge("or-key", client=http)
        with pytest.raises(ReviewOpinionSchemaError):
            await judge.judge({"fix": {}, "conflict": {"hunks": []}}, "some/model")


async def test_openrouter_judge_raises_on_non_json_body():
    async with httpx.AsyncClient(transport=httpx.MockTransport(_non_json)) as http:
        judge = OpenRouterJudge("or-key", client=http)
        with pytest.raises(ReviewOpinionSchemaError):
            await judge.judge({"fix": {}, "conflict": {"hunks": []}}, "some/model")


async def test_fake_judge_records_model_and_counts_calls():
    fake = FakeJudge()
    op = await fake.judge({"x": 1}, "anthropic/sonnet")
    assert isinstance(op, ReviewOpinion)
    assert fake.calls == 1 and fake.last_model == "anthropic/sonnet"
```

- [ ] **Step 2: Run it, expect failure** — `python -m pytest tests/test_review_judge.py -q` → FAIL (modules missing).

- [ ] **Step 3a: Implement `src/mai/judge/prompt.py`:**

```python
# src/mai/judge/prompt.py
import json

PROMPT_VERSION = 1

SYSTEM_PROMPT = (
    "You are a porting advisor for the getMaNGOS World of Warcraft emulator forks. "
    "You are shown a fix from a source fork and evidence about how it applies to a "
    "target fork: which patch hunks apply vs reject, the target's current code at "
    "those spots (best-effort, source-fork line numbers), similar commits already in "
    "the target, and the fix's intent. Judge ONLY from this evidence whether the fix "
    "belongs in the target. NEVER invent code, files, or commits you were not shown. "
    "Every tip and every citation MUST quote a file path, a 'path:line', or a commit "
    "sha that appears verbatim in the evidence — ungrounded claims are discarded and "
    "lower your confidence. If the evidence is insufficient, use assessment "
    "\"uncertain\". Respond with ONLY a single JSON object with keys: assessment "
    "(portable|already_handled|divergent|uncertain), confidence (0.0-1.0), reason "
    "(string), tips (list of strings), adapted_hunks (list of {path, suggestion}), "
    "citations (list of strings)."
)

_CAP = 24000


def build_prompt(evidence: dict) -> str:
    """Render the evidence packet compactly for the judge (capped)."""
    blob = json.dumps(evidence, separators=(",", ":"))[:_CAP]
    return "Review evidence (JSON):\n" + blob + "\n\nReturn the JSON opinion object."
```

- [ ] **Step 3b: Implement `src/mai/judge/judge.py`:**

```python
# src/mai/judge/judge.py
from typing import Protocol

import httpx

from mai.judge.prompt import SYSTEM_PROMPT, build_prompt
from mai.judge.schema import ReviewOpinion, ReviewOpinionSchemaError, parse_opinion


class ReviewJudge(Protocol):
    async def judge(self, evidence: dict, model: str) -> ReviewOpinion: ...


def choose_model(evidence: dict, settings) -> str:
    """Pick the large-context model for many-hunk / large fixes, else the default."""
    conflict = evidence.get("conflict") or {}
    total = conflict.get("total") or 0
    size = 0
    for h in conflict.get("hunks") or []:
        size += len(h.get("patch_text") or "") + len(h.get("target_context") or "")
    if (total > settings.review_hunk_routing_threshold
            or size > settings.review_large_context_chars):
        return settings.review_model_large
    return settings.review_model


class OpenRouterJudge:
    """Production ReviewJudge backed by OpenRouter chat completions."""

    def __init__(self, api_key: str, base_url: str = "https://openrouter.ai/api",
                 client: httpx.AsyncClient | None = None):
        self._base = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient()
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    async def judge(self, evidence: dict, model: str) -> ReviewOpinion:
        resp = await self._client.post(
            self._base + "/v1/chat/completions",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": build_prompt(evidence)},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0,
            },
            headers=self._headers,
        )
        resp.raise_for_status()
        try:
            content = resp.json()["choices"][0]["message"].get("content")
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ReviewOpinionSchemaError(f"malformed OpenRouter response: {exc}") from exc
        if not content:
            raise ReviewOpinionSchemaError("OpenRouter returned empty/null message content")
        return parse_opinion(content)
```

- [ ] **Step 3c: Implement `src/mai/judge/fake.py`:**

```python
# src/mai/judge/fake.py
from mai.judge.schema import ReviewOpinion


class FakeJudge:
    """Deterministic ReviewJudge for tests. Records call count + last model."""

    def __init__(self, opinion: ReviewOpinion | None = None,
                 raises: Exception | None = None):
        self._opinion = opinion or ReviewOpinion(
            assessment="portable", confidence=0.8, reason="ok")
        self._raises = raises
        self.calls = 0
        self.last_model: str | None = None

    async def judge(self, evidence: dict, model: str) -> ReviewOpinion:
        self.calls += 1
        self.last_model = model
        if self._raises is not None:
            raise self._raises
        return self._opinion
```

- [ ] **Step 4: Run tests, expect pass** — `python -m pytest tests/test_review_judge.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/mai/judge/prompt.py src/mai/judge/judge.py src/mai/judge/fake.py tests/test_review_judge.py
git commit -m "feat: ReviewJudge seam + content-aware model router (Sonnet default, Gemini for large)"
```

---

### Task 4: `build_review_advice` orchestration

**Files:**
- Modify: `src/mai/sync/review.py`
- Test: `tests/test_review_advice.py`

**Interfaces:**
- Consumes: `build_review_evidence` (P1, same module), `choose_model` (`mai.judge.judge`), `ground_opinion` (`mai.judge.ground`), `settings` (`mai.config`).
- Produces: `async build_review_advice(session, git_client, judge, item_id, *, settings=settings) -> dict` returning `{"evidence": <packet|None>, "opinion": <dict|None>}`. Calls the judge only when `judge is not None` AND evidence is non-None (review item). Any judge exception → `opinion=None`.

- [ ] **Step 1: Write the failing test** — reuse the seeded-review fixture shape from `tests/test_review_evidence.py` (PatchGroup + Commit + CommitFile + a `review` PortVerdict; FakeGitClient with `_diffs`/`_rejected`/`_regions`/`_logs`). Read that file and mirror its `session` fixture.

```python
# tests/test_review_advice.py
from datetime import datetime, timezone
import pytest_asyncio
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mai.db.base import Base
from mai.db.models import PatchGroup, Commit, CommitFile, PortVerdict
from mai.git.fake import FakeGitClient
from mai.judge.fake import FakeJudge
from mai.judge.schema import ReviewOpinion
from mai.sync.review import build_review_advice

PATCH = ("diff --git a/src/shared/Db.cpp b/src/shared/Db.cpp\n"
         "--- a/src/shared/Db.cpp\n+++ b/src/shared/Db.cpp\n"
         "@@ -1,3 +1,4 @@\n a\n b\n+x\n c\n"
         "@@ -6,3 +7,3 @@\n ctx\n g\n-h\n+H\n")
REJ = "@@ -6,3 +7,3 @@\n ctx\n g\n-h\n+H\n"


@pytest_asyncio.fixture
async def session():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    f = async_sessionmaker(eng, expire_on_commit=False)
    async with f() as s:
        s.add(PatchGroup(id="pg1", patch_id="p1"))
        ts = datetime(2026, 6, 1, tzinfo=timezone.utc)
        cm = Commit(core="three", sha="sha123abcd", author="a", authored_at=ts,
                    committer="a", committed_at=ts, message="db crash fix on shutdown")
        s.add(cm)
        await s.flush()
        s.add(CommitFile(commit_id=cm.id, path="src/shared/Db.cpp", subsystem="src/shared",
                         change_type="M", added_lines=2, removed_lines=1))
        s.add(PortVerdict(patch_group_id="pg1", core="four", verdict="review",
                          apply_result="conflict", relevance="portable", source_core="three",
                          source_sha="sha123abcd", subsystem="src/shared", magnitude=3,
                          tier="surgical", conflict_applied=1, conflict_total=2))
        await s.commit()
        yield s


def _git():
    git = FakeGitClient()
    git._diffs = {("three", "sha123abcd"): PATCH}
    git._rejected = {("four", PATCH): {"src/shared/Db.cpp": REJ}}
    git._regions = {("four", "src/shared/Db.cpp"): "g\nH\ni"}
    git._logs = {"four": []}
    return git


async def test_advice_returns_evidence_and_grounded_opinion(session):
    op = ReviewOpinion(assessment="portable", confidence=0.9, reason="clean",
                       tips=["adapt in src/shared/Db.cpp"], citations=["src/shared/Db.cpp"])
    judge = FakeJudge(opinion=op)
    out = await build_review_advice(session, _git(), judge, "pg1:four")
    assert out["evidence"]["core"] == "four"
    assert out["opinion"]["assessment"] == "portable"
    assert "adapt in src/shared/Db.cpp" in out["opinion"]["tips"]
    assert judge.calls == 1


async def test_advice_no_judge_yields_null_opinion(session):
    out = await build_review_advice(session, _git(), None, "pg1:four")
    assert out["evidence"] is not None
    assert out["opinion"] is None


async def test_invariant1_non_review_never_calls_judge(session):
    await session.execute(update(PortVerdict).values(verdict="needs"))
    await session.commit()
    judge = FakeJudge()
    out = await build_review_advice(session, _git(), judge, "pg1:four")
    assert out["evidence"] is None       # build_review_evidence returns None for non-review
    assert out["opinion"] is None
    assert judge.calls == 0              # Invariant 1: judge never invoked


async def test_judge_failure_degrades_to_null_opinion(session):
    judge = FakeJudge(raises=RuntimeError("boom"))
    out = await build_review_advice(session, _git(), judge, "pg1:four")
    assert out["evidence"] is not None
    assert out["opinion"] is None        # exception swallowed, evidence preserved
```

- [ ] **Step 2: Run it, expect failure** — `python -m pytest tests/test_review_advice.py -q` → FAIL (`build_review_advice` undefined).

- [ ] **Step 3: Implement** — append to `src/mai/sync/review.py` (and add the imports at the top, beside the existing imports):

```python
# add near the top of src/mai/sync/review.py, with the other imports
from mai.config import settings as _settings
from mai.judge.ground import ground_opinion
from mai.judge.judge import choose_model
```

```python
# append at the end of src/mai/sync/review.py
async def build_review_advice(session, git_client, judge, item_id, *, settings=_settings):
    """Collect evidence (P1), then — only for a real review item and only when a judge
    is provided — get a grounded LLM opinion. Any judge failure degrades to opinion=None;
    the evidence is always returned. Invariant 1: non-review -> evidence None, no judge call."""
    evidence = await build_review_evidence(session, git_client, item_id)
    if evidence is None or judge is None:
        return {"evidence": evidence, "opinion": None}
    try:
        model = choose_model(evidence, settings)
        raw = await judge.judge(evidence, model)
        opinion = ground_opinion(raw, evidence).model_dump()
    except Exception:  # noqa: BLE001 — a judge/network/schema failure must never 500
        opinion = None
    return {"evidence": evidence, "opinion": opinion}
```

- [ ] **Step 4: Run the test + full suite** — `python -m pytest tests/test_review_advice.py -q` → PASS, then `python -m pytest -q` → green.

- [ ] **Step 5: Commit**

```bash
git add src/mai/sync/review.py tests/test_review_advice.py
git commit -m "feat: build_review_advice — collect, judge, ground (Invariant 1 + fail-safe)"
```

---

### Task 5: API wiring + config

**Files:**
- Modify: `src/mai/config.py`, `src/mai/web/review_api.py`, `src/mai/web/app.py`
- Test: `tests/test_review_api.py`

**Interfaces:**
- Consumes: `build_review_advice` (Task 4), `OpenRouterJudge` (Task 3), `settings` (Task 5 config).
- Produces: `make_review_router(session_factory, git_client=None, judge=None)`; `GET /api/review/{item_id}` → `{"evidence": ..., "opinion": ...}`; `create_app(..., review_judge=None)` passes the judge through.

- [ ] **Step 1: Add config** — in `src/mai/config.py`, beside the existing OpenRouter/model fields:

```python
    review_advisor_enabled: bool = False
    review_model: str = "anthropic/claude-sonnet-4.6"      # OpenRouter slug; verify in catalog
    review_model_large: str = "google/gemini-2.5-pro"      # OpenRouter slug; verify in catalog
    review_hunk_routing_threshold: int = 8
    review_large_context_chars: int = 24000
```

> Before committing, verify the two slugs resolve on OpenRouter's model list. If a slug is not exactly right, set the closest current Claude-Sonnet and Gemini-Pro slugs and note it in the report — they are config-overridable, so do not block on it.

- [ ] **Step 2: Write the failing test** — extend `tests/test_review_api.py` (read its existing ASGI + login fixture and the `review_git_client` injection it already uses; add a `review_judge` injection in the same place). Add:

```python
async def test_review_api_includes_opinion_when_judge_injected(client_factory):
    # client_factory builds the app with create_app(..., review_git_client=<fake>,
    # review_judge=FakeJudge(...)) and a logged-in session — mirror the existing
    # test_review_api fixtures exactly.
    from mai.judge.fake import FakeJudge
    from mai.judge.schema import ReviewOpinion
    judge = FakeJudge(opinion=ReviewOpinion(assessment="divergent", confidence=0.4,
                                            reason="differs"))
    app_client = await client_factory(review_judge=judge)
    resp = await app_client.get("/api/review/pg1:four")
    assert resp.status_code == 200
    body = resp.json()
    assert "evidence" in body and "opinion" in body
    assert body["opinion"]["assessment"] == "divergent"


async def test_review_api_opinion_null_without_judge(client_factory):
    app_client = await client_factory(review_judge=None)
    resp = await app_client.get("/api/review/pg1:four")
    assert resp.status_code == 200
    assert resp.json()["opinion"] is None
```

> If the existing `test_review_api.py` does not already have a reusable `client_factory`, adapt these to its actual fixture style (it injects `review_git_client` today — add `review_judge` the same way). Keep the existing anon→303 and review→200 tests passing.

- [ ] **Step 3: Run it, expect failure** — `python -m pytest tests/test_review_api.py -q` → FAIL (opinion key / `review_judge` param missing).

- [ ] **Step 4: Implement `src/mai/web/review_api.py`:**

```python
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from mai.config import settings
from mai.git.client import LocalGitClient
from mai.sync.review import build_review_advice


def _default_judge():
    if settings.review_advisor_enabled and settings.openrouter_api_key:
        from mai.judge.judge import OpenRouterJudge
        return OpenRouterJudge(settings.openrouter_api_key, settings.openrouter_api_url)
    return None


def make_review_router(session_factory, git_client=None, judge=None) -> APIRouter:
    """GET /api/review/{item_id} — evidence + grounded advisory opinion for a REVIEW item."""
    router = APIRouter(prefix="/api/review")
    client = git_client or LocalGitClient(settings.git_mirror_dir)
    active_judge = judge if judge is not None else _default_judge()

    @router.get("/{item_id}")
    async def get_review(request: Request, item_id: str):
        async with session_factory() as session:
            result = await build_review_advice(session, client, active_judge, item_id)
        return JSONResponse(result)

    return router
```

In `src/mai/web/app.py`, thread a `review_judge=None` parameter through `create_app` exactly the way `review_git_client` is already threaded, and pass it: `make_review_router(session_factory, review_git_client, review_judge)`. (Read the current `create_app` signature + the `make_review_router(...)` call site and mirror the existing injection.)

- [ ] **Step 5: Run the test + full suite** — both green.

- [ ] **Step 6: Commit**

```bash
git add src/mai/config.py src/mai/web/review_api.py src/mai/web/app.py tests/test_review_api.py
git commit -m "feat: serve {evidence, opinion} from /api/review; review-advisor config + judge wiring"
```

---

### Task 6: Board opinion panel

**Files:**
- Modify: `src/mai/web/static/portboard.js`, `src/mai/web/static/board.css`

**Interfaces:**
- Consumes: `GET /api/review/{item_id}` now returns `{evidence, opinion}` (Task 5).
- Produces: when `opinion` is non-null, an opinion block rendered ABOVE/below the existing evidence (assessment badge, grounded-confidence %, tips, adapted-hunk suggestions). When null, the panel is exactly the P1 evidence view.

- [ ] **Step 1: Read the current `renderEvidence` flow** in `portboard.js` (added in P1). Confirm the lazy-fetch handler stores `j` and calls `renderEvidence(j.evidence)`. Change it to render both: `renderAdvice(j.opinion) + renderEvidence(j.evidence)`.

Update the fetch `.then` body to:

```javascript
        proof.dataset.loaded = "1";
        proof.innerHTML = j.evidence
          ? (renderAdvice(j.opinion) + renderEvidence(j.evidence))
          : "<div class='rev-load'>no evidence (not a review item)</div>";
```

- [ ] **Step 2: Add `renderAdvice(op)` to `portboard.js`** (near `renderEvidence`; reuse the existing `esc` helper):

```javascript
function renderAdvice(op) {
  if (!op) return "";
  const pct = Math.round((op.confidence || 0) * 100);
  const tips = (op.tips || []).map(t => `<li>${esc(t)}</li>`).join("");
  const hunks = (op.adapted_hunks || []).map(h =>
    `<div class="rev-op-hunk"><code>${esc(h.path)}</code> ${esc(h.suggestion)}</div>`).join("");
  return `<div class="rev-op rev-op-${esc(op.assessment)}">
    <div class="rev-op-head">
      <span class="rev-op-badge">${esc(op.assessment)}</span>
      <span class="rev-op-conf">grounded confidence ${pct}%</span>
    </div>
    <div class="rev-op-reason">${esc(op.reason || "")}</div>
    ${tips ? `<ul class="rev-op-tips">${tips}</ul>` : ""}
    ${hunks}
  </div>`;
}
```

- [ ] **Step 3: Add `.rev-op*` CSS** to `board.css` — a bordered advisory box, the assessment badge colour-coded (portable=green, already_handled=blue, divergent=amber, uncertain=grey), a muted confidence label, monospace `code` for paths. Keep the existing light theme; read the surrounding rules and match the palette/variables. Do not restyle anything else.

- [ ] **Step 4: Validate** — `node --check src/mai/web/static/portboard.js` MUST pass (run it; paste output). Confirm the keys `renderAdvice` reads (`assessment`, `confidence`, `reason`, `tips`, `adapted_hunks[].path/.suggestion`) match `ReviewOpinion` (Task 1). Do NOT start/stop the web server — the controller performs the live visual validation. No unit test for this task by design.

- [ ] **Step 5: Commit**

```bash
git add src/mai/web/static/portboard.js src/mai/web/static/board.css
git commit -m "feat: render grounded review opinion (assessment, confidence, tips) in the panel"
```

---

## Self-Review

- **Spec coverage (`review-advisor-p2-design.md`):** §2 layout → Tasks 1–3; §3 schema → Task 1; §4 router → Task 3; §5 config → Task 5; §6 flow + §7 grounding → Tasks 2 & 4; §8 errors → Tasks 4 & 5; §9 UI → Task 6; §10 tests → spread across each task's TDD. ✅
- **Invariant 1:** `build_review_advice` returns evidence-None / judge-uncalled for non-review (Task 4 `test_invariant1_non_review_never_calls_judge`). ✅
- **Invariant 2 (grounding):** Task 2 headline tests — ungrounded dropped, all-ungrounded → uncertain/0, grounded survives with proportional confidence. ✅
- **Type consistency:** `ReviewOpinion` fields (Task 1) == grounded fields (Task 2) == `build_review_advice` `model_dump()` (Task 4) == `renderAdvice` reads (Task 6); `judge(evidence, model)` signature identical across Protocol/OpenRouterJudge/FakeJudge (Task 3) and the `build_review_advice` call (Task 4). ✅
- **Offline / fail-safe:** no key/disabled → `_default_judge()` None → `opinion=None` (Task 5); judge exception → `opinion=None` (Task 4). ✅
- **Zero-retry:** `build_review_advice` makes exactly one `judge.judge` call inside the try. ✅
- **Placeholder scan:** no TBD/TODO; the only deferred verification is the two OpenRouter slugs, with an explicit fallback instruction (Task 5 Step 1). ✅

## Execution Handoff

Recommended: **subagent-driven-development** (fresh implementer + task review per task, final whole-branch review), same as P1.
