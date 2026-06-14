# Mai Publish (Hugo Site) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Project the canonical store into a Hugo content tree — bug pages with their enrichment, verdict, and evidence; per-pair drift tables; a home dashboard — so the whole thing is viewable as a fast static site. Fully offline (no Cloudflare/Neon needed).

**Architecture:** A `views` layer gathers each report's bundle (report + latest enrichment + verification + correlations) and the drift observations by fork-pair, all through repositories. Pure `render` functions turn each bundle into `.md` with versioned front-matter. `publish_site` writes the content tree into the ledger dir. Minimal Hugo scaffolding (config + layouts) makes `hugo build` produce the site. Tests assert generated `.md`; the actual `hugo` build is an optional smoke step.

**Tech Stack:** Python 3.12 · SQLAlchemy 2.0 async · pytest + pytest-asyncio (uses `tmp_path`) · Hugo (optional, for the build smoke only).

---

## Builds on Plans 01–07

Reuse as-is (do NOT redefine):
- `mai.db.models` (`Report`, `Enrichment`, `Verification`, `Correlation`, `DriftObservation`).
- `mai.repository.reports.ReportRepository` (`.all_reports()`, `.get_by_id()`); `mai.repository.correlation.{CorrelationRepository,VerificationRepository}`.
- `mai.config.settings.ledger_path`; `tests/conftest.py` `session` fixture; CLI patterns.
- Plan 01's `publish/markdown.py` stays (its `report_to_markdown` is the v1 projection); this plan adds the richer v2 page renderer in `publish/render.py` and repoints the CLI.

**Design principles:** publish is a **deterministic projection** of the store (re-runnable, derived); reads go through the `views`/repository layer; front-matter is a versioned contract (Hugo + future models consume it).

## File Structure

```
src/mai/publish/
  __init__.py            # EXISTING
  markdown.py            # EXISTING (v1 report_to_markdown) — untouched
  views.py               # report_bundle, iter_bug_reports, drift_observations_by_pair, counts
  render.py              # render_report_page, render_drift_page, render_home (v2)
  site.py                # publish_site (writes the content tree)
src/mai/cli/__main__.py  # MODIFY: repoint _publish to publish_site
mai-data/                # the ledger + Hugo site root (committed scaffolding)
  hugo.toml
  layouts/_default/baseof.html
  layouts/_default/single.html
  layouts/_default/list.html
  layouts/index.html
tests/
  test_publish_views.py
  test_publish_render.py
  test_publish_site.py
```

---

### Task 1: Views layer

**Files:**
- Create: `mai/src/mai/publish/views.py`
- Create: `mai/tests/test_publish_views.py`

- [ ] **Step 1: Write the failing test**

`mai/tests/test_publish_views.py`:

```python
from mai.contracts import IntakeEvent
from mai.ingest import ingest_event
from mai.publish.views import (
    counts, drift_observations_by_pair, iter_bug_reports, report_bundle,
)
from mai.repository.correlation import CorrelationRepository, VerificationRepository
from mai.repository.drift import DriftRepository
from mai.repository.enrichment import EnrichmentRepository
from mai.repository.reports import ReportRepository

STATS = {"shared": 5, "diverged": 3, "identical": 2, "only_a": 0, "only_b": 1}


async def _seed(session):
    await ingest_event(session, IntakeEvent("ips", "r1", "Pet bug", "zero",
                                            raw_payload={"markdown": "x"}))
    await ingest_event(session, IntakeEvent("gh_pr", "zero/server#7", "Fix", "zero",
                                            status="merged", raw_payload={"body": "y"}))
    await session.commit()
    rr = ReportRepository(session)
    bug = await rr.get_report("ips:r1")
    pr = await rr.get_report("gh_pr:zero/server#7")
    EnrichmentRepository(session).add(
        report_id=bug.id, model="m", prompt_version=1, schema_version=1, input_hash="h",
        result={"normalized_title": "Pet threat", "english_summary": "Pet loses threat."},
        needs_human_review=False)
    await CorrelationRepository(session).upsert(bug.id, pr.id, "explicit_ref", 1.0)
    await VerificationRepository(session).upsert(bug.id, "fixed_confirmed", 0.95, [])
    await DriftRepository(session).upsert("zero/server", "two/server", "src/game/Object", STATS)
    await session.commit()
    return bug


async def test_report_bundle_gathers_everything(session):
    bug = await _seed(session)
    b = await report_bundle(session, bug)
    assert b.enrichment["normalized_title"] == "Pet threat"
    assert b.verification.verdict == "fixed_confirmed"
    assert b.correlations == [("gh_pr:zero/server#7", "explicit_ref", 1.0)]


async def test_iter_bug_reports_excludes_prs(session):
    await _seed(session)
    keys = [r.canonical_key for r in await iter_bug_reports(session)]
    assert keys == ["ips:r1"]  # the gh_pr is not a bug


async def test_drift_observations_grouped_by_pair(session):
    await _seed(session)
    grouped = await drift_observations_by_pair(session)
    assert list(grouped.keys()) == [("zero/server", "two/server")]
    assert grouped[("zero/server", "two/server")][0].diverged == 3


async def test_counts_summarizes_store(session):
    await _seed(session)
    c = await counts(session)
    assert c["reports"] == 2
    assert c["enriched"] == 1
    assert c["fixed_confirmed"] == 1
    assert c["open"] == 0
    assert c["drift_pairs"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mai && pytest tests/test_publish_views.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mai.publish.views'`

- [ ] **Step 3: Write `publish/views.py`**

```python
from dataclasses import dataclass

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mai.db.models import DriftObservation, Enrichment, Report, Verification
from mai.repository.correlation import CorrelationRepository, VerificationRepository
from mai.repository.reports import ReportRepository

_BUG_PREFIXES = ("ips:", "gh_issue:")


@dataclass
class ReportBundle:
    report: Report
    enrichment: dict | None
    verification: Verification | None
    correlations: list[tuple[str, str, float]]


async def report_bundle(session: AsyncSession, report: Report) -> ReportBundle:
    enr = await session.scalar(
        select(Enrichment).where(Enrichment.report_id == report.id)
        .order_by(desc(Enrichment.created_at)).limit(1)
    )
    ver = await VerificationRepository(session).get(report.id)
    rr = ReportRepository(session)
    corrs = []
    for c in await CorrelationRepository(session).for_report(report.id):
        related = await rr.get_by_id(c.related_report_id)
        key = related.canonical_key if related else c.related_report_id
        corrs.append((key, c.method, c.score))
    return ReportBundle(report=report,
                        enrichment=enr.result if enr else None,
                        verification=ver, correlations=corrs)


async def iter_bug_reports(session: AsyncSession) -> list[Report]:
    reports = await ReportRepository(session).all_reports()
    return [r for r in reports if r.canonical_key.startswith(_BUG_PREFIXES)]


async def drift_observations_by_pair(
        session: AsyncSession) -> dict[tuple[str, str], list[DriftObservation]]:
    grouped: dict[tuple[str, str], list[DriftObservation]] = {}
    for o in await session.scalars(select(DriftObservation)):
        grouped.setdefault((o.fork_a, o.fork_b), []).append(o)
    return grouped


async def counts(session: AsyncSession) -> dict:
    async def _count(stmt) -> int:
        return await session.scalar(stmt) or 0

    return {
        "reports": await _count(select(func.count()).select_from(Report)),
        "enriched": await _count(
            select(func.count(func.distinct(Enrichment.report_id)))),
        "open": await _count(select(func.count()).select_from(Verification)
                             .where(Verification.verdict == "open")),
        "likely_fixed": await _count(select(func.count()).select_from(Verification)
                                     .where(Verification.verdict == "likely_fixed")),
        "fixed_confirmed": await _count(select(func.count()).select_from(Verification)
                                        .where(Verification.verdict == "fixed_confirmed")),
        "drift_pairs": len(await drift_observations_by_pair(session)),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd mai && pytest tests/test_publish_views.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add mai/src/mai/publish/views.py mai/tests/test_publish_views.py
git commit -m "feat: publish views (report bundle, bug filter, drift grouping, counts)"
```

---

### Task 2: Page renderers

**Files:**
- Create: `mai/src/mai/publish/render.py`
- Create: `mai/tests/test_publish_render.py`

- [ ] **Step 1: Write the failing test**

`mai/tests/test_publish_render.py`:

```python
from mai.db.models import DriftObservation, Report, Verification
from mai.publish.render import render_drift_page, render_home, render_report_page
from mai.publish.views import ReportBundle


def _bundle(**kw):
    base = dict(
        report=Report(canonical_key="ips:r1", core="zero", title="Raw title", status="completed"),
        enrichment={"normalized_title": "Pet threat", "english_summary": "Pet loses threat.",
                    "steps_to_reproduce": ["attack", "send pet"],
                    "affected_entities": {"npc": ["Devilsaur"], "zone": []}},
        verification=Verification(report_id="x", verdict="fixed_confirmed", confidence=0.95,
                                  evidence=[]),
        correlations=[("gh_pr:zero/server#7", "explicit_ref", 1.0)])
    base.update(kw)
    return ReportBundle(**base)


def test_render_report_page_full():
    md = render_report_page(_bundle())
    assert md.startswith("---\n")
    assert "schema_version: 2" in md
    assert "id: ips:r1" in md
    assert 'title: "Pet threat"' in md          # enriched title wins over raw
    assert "verdict: fixed_confirmed" in md
    assert "confidence: 0.95" in md
    assert "## Summary" in md and "Pet loses threat." in md
    assert "## Steps to reproduce" in md and "- attack" in md
    assert "**npc:** Devilsaur" in md            # empty zone list omitted
    assert "## Evidence" in md
    assert "`gh_pr:zero/server#7` (explicit_ref, score 1.00)" in md


def test_render_report_page_minimal_falls_back_to_raw_title():
    md = render_report_page(_bundle(enrichment=None, verification=None, correlations=[]))
    assert 'title: "Raw title"' in md
    assert "verdict: open" in md
    assert "## Summary" not in md               # nothing to summarize


def test_render_drift_page_sorts_by_diverged():
    obs = [
        DriftObservation(fork_a="a", fork_b="b", subsystem="low", shared=2, diverged=1,
                         identical=1, only_a=0, only_b=0),
        DriftObservation(fork_a="a", fork_b="b", subsystem="high", shared=9, diverged=8,
                         identical=1, only_a=0, only_b=0),
    ]
    md = render_drift_page("a", "b", obs)
    assert 'title: "Drift: a vs b"' in md
    assert "| Subsystem |" in md
    assert md.index("high") < md.index("low")   # most-diverged first


def test_render_home_shows_counts():
    md = render_home({"reports": 10, "enriched": 7, "open": 3, "likely_fixed": 2,
                      "fixed_confirmed": 1, "drift_pairs": 6})
    assert "**Reports:** 10" in md
    assert "fixed_confirmed 1" in md
    assert "**Drift pairs:** 6" in md
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mai && pytest tests/test_publish_render.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mai.publish.render'`

- [ ] **Step 3: Write `publish/render.py`**

```python
from mai.db.models import DriftObservation
from mai.publish.views import ReportBundle

SCHEMA_VERSION = 2


def _q(text: str) -> str:
    """Quote a front-matter string value."""
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_report_page(bundle: ReportBundle) -> str:
    r = bundle.report
    enr = bundle.enrichment or {}
    title = enr.get("normalized_title") or r.title or r.canonical_key
    ver = bundle.verification
    verdict = ver.verdict if ver else "open"
    confidence = ver.confidence if ver else 0.0

    lines = ["---", f"schema_version: {SCHEMA_VERSION}", f"id: {r.canonical_key}",
             f"title: {_q(title)}", f"core: {r.core}", f"status: {r.status}",
             f"verdict: {verdict}", f"confidence: {confidence}", "---", "",
             f"# {title}", "", f"**Verdict:** {verdict} (confidence {confidence})", ""]

    summary = enr.get("english_summary")
    if summary:
        lines += ["## Summary", "", summary, ""]

    steps = enr.get("steps_to_reproduce") or []
    if steps:
        lines += ["## Steps to reproduce", ""] + [f"- {s}" for s in steps] + [""]

    entities = enr.get("affected_entities") or {}
    ent_lines = [f"- **{k}:** {', '.join(v)}" for k, v in entities.items() if v]
    if ent_lines:
        lines += ["## Affected", ""] + ent_lines + [""]

    if bundle.correlations:
        lines += ["## Evidence", ""]
        lines += [f"- `{key}` ({method}, score {score:.2f})"
                  for key, method, score in bundle.correlations]
        lines += [""]

    return "\n".join(lines).rstrip() + "\n"


def render_drift_page(fork_a: str, fork_b: str,
                      observations: list[DriftObservation]) -> str:
    title = f"Drift: {fork_a} vs {fork_b}"
    lines = ["---", f"schema_version: {SCHEMA_VERSION}", f"title: {_q(title)}",
             "type: drift", f"fork_a: {fork_a}", f"fork_b: {fork_b}", "---", "",
             f"# {title}", "",
             "| Subsystem | Shared | Diverged | Identical | Only A | Only B |",
             "|---|---|---|---|---|---|"]
    for o in sorted(observations, key=lambda o: o.diverged, reverse=True):
        lines.append(f"| {o.subsystem} | {o.shared} | {o.diverged} | {o.identical} "
                     f"| {o.only_a} | {o.only_b} |")
    return "\n".join(lines).rstrip() + "\n"


def render_home(counts: dict) -> str:
    lines = ["---", f'title: {_q("Mai — getMaNGOS Bug & Drift Observatory")}',
             "---", "", "# Mai — getMaNGOS Bug & Drift Observatory", "",
             f"- **Reports:** {counts['reports']}",
             f"- **Enriched:** {counts['enriched']}",
             f"- **Verdicts:** open {counts['open']} · likely_fixed {counts['likely_fixed']} "
             f"· fixed_confirmed {counts['fixed_confirmed']}",
             f"- **Drift pairs:** {counts['drift_pairs']}"]
    return "\n".join(lines).rstrip() + "\n"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd mai && pytest tests/test_publish_render.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add mai/src/mai/publish/render.py mai/tests/test_publish_render.py
git commit -m "feat: v2 page renderers (report w/ verdict+evidence, drift table, home)"
```

---

### Task 3: publish_site orchestration

**Files:**
- Create: `mai/src/mai/publish/site.py`
- Create: `mai/tests/test_publish_site.py`

- [ ] **Step 1: Write the failing test**

`mai/tests/test_publish_site.py`:

```python
from pathlib import Path

from mai.contracts import IntakeEvent
from mai.ingest import ingest_event
from mai.publish.site import publish_site
from mai.repository.drift import DriftRepository

STATS = {"shared": 5, "diverged": 3, "identical": 2, "only_a": 0, "only_b": 1}


async def test_publish_site_writes_home_bug_and_drift(session, tmp_path):
    await ingest_event(session, IntakeEvent("ips", "r1", "Pet bug", "zero",
                                            raw_payload={"markdown": "x"}))
    await DriftRepository(session).upsert("mangoszero/server", "mangostwo/server",
                                          "src/game/Object", STATS)
    await session.commit()
    written = await publish_site(session, str(tmp_path))
    content = tmp_path / "content"
    assert (content / "_index.md").exists()
    bug = content / "zero" / "bugs" / "ips-r1.md"
    assert bug.exists()
    assert "Pet bug" in bug.read_text(encoding="utf-8")
    drift = content / "sync" / "mangoszero-server--vs--mangostwo-server.md"
    assert drift.exists()
    assert "src/game/Object" in drift.read_text(encoding="utf-8")
    assert written == 3  # home + 1 bug + 1 drift


async def test_publish_site_excludes_pr_reports(session, tmp_path):
    await ingest_event(session, IntakeEvent("gh_pr", "zero/server#7", "Fix", "zero",
                                            status="merged", raw_payload={"body": "y"}))
    await session.commit()
    await publish_site(session, str(tmp_path))
    # only the home page is written; the PR is not a bug page
    assert not (tmp_path / "content" / "zero" / "bugs").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd mai && pytest tests/test_publish_site.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mai.publish.site'`

- [ ] **Step 3: Write `publish/site.py`**

```python
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from mai.publish.render import render_drift_page, render_home, render_report_page
from mai.publish.views import (
    counts, drift_observations_by_pair, iter_bug_reports, report_bundle,
)


def _safe(key: str) -> str:
    return key.replace(":", "-").replace("/", "-").replace("#", "-")


async def publish_site(session: AsyncSession, out_dir: str) -> int:
    """Project the store into a Hugo content tree under out_dir/content. Returns files written."""
    content = Path(out_dir) / "content"
    content.mkdir(parents=True, exist_ok=True)
    written = 0

    (content / "_index.md").write_text(render_home(await counts(session)), encoding="utf-8")
    written += 1

    for report in await iter_bug_reports(session):
        bundle = await report_bundle(session, report)
        target = content / report.core / "bugs"
        target.mkdir(parents=True, exist_ok=True)
        (target / f"{_safe(report.canonical_key)}.md").write_text(
            render_report_page(bundle), encoding="utf-8")
        written += 1

    pairs = await drift_observations_by_pair(session)
    if pairs:
        sync = content / "sync"
        sync.mkdir(parents=True, exist_ok=True)
        for (fork_a, fork_b), observations in pairs.items():
            slug = f"{_safe(fork_a)}--vs--{_safe(fork_b)}"
            (sync / f"{slug}.md").write_text(
                render_drift_page(fork_a, fork_b, observations), encoding="utf-8")
            written += 1

    return written
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd mai && pytest tests/test_publish_site.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add mai/src/mai/publish/site.py mai/tests/test_publish_site.py
git commit -m "feat: publish_site writes the Hugo content tree (home + bugs + drift)"
```

---

### Task 4: Hugo scaffolding

**Files:**
- Create: `mai/mai-data/hugo.toml`
- Create: `mai/mai-data/layouts/_default/baseof.html`
- Create: `mai/mai-data/layouts/_default/single.html`
- Create: `mai/mai-data/layouts/_default/list.html`
- Create: `mai/mai-data/layouts/index.html`

(No tests — static scaffolding. The `hugo` build is the optional smoke in Task 5.)

- [ ] **Step 1: Write `mai-data/hugo.toml`**

```toml
baseURL = "/"
languageCode = "en-us"
title = "Mai — getMaNGOS Bug & Drift Observatory"
disableKinds = ["taxonomy", "term", "RSS", "sitemap"]

[markup.goldmark.renderer]
unsafe = true
```

- [ ] **Step 2: Write `mai-data/layouts/_default/baseof.html`**

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{ .Title }} · Mai</title>
  <style>
    body{font-family:system-ui,sans-serif;max-width:1000px;margin:2rem auto;padding:0 1rem;line-height:1.5}
    nav a{margin-right:1rem}
    table{border-collapse:collapse;width:100%}
    th,td{border:1px solid #ddd;padding:.3rem .5rem;text-align:left}
    code{background:#f4f4f4;padding:.1rem .3rem;border-radius:3px}
  </style>
</head>
<body>
  <nav><a href="/">Home</a><a href="/sync/">Drift</a></nav>
  <hr>
  {{ block "main" . }}{{ end }}
</body>
</html>
```

- [ ] **Step 3: Write `mai-data/layouts/_default/single.html`**

```html
{{ define "main" }}
  {{ .Content }}
{{ end }}
```

- [ ] **Step 4: Write `mai-data/layouts/_default/list.html`**

```html
{{ define "main" }}
  {{ .Content }}
  <ul>
    {{ range .Pages }}
      <li><a href="{{ .RelPermalink }}">{{ .Title }}</a>
        {{ with .Params.verdict }}<em>— {{ . }}</em>{{ end }}</li>
    {{ end }}
  </ul>
{{ end }}
```

- [ ] **Step 5: Write `mai-data/layouts/index.html`**

```html
{{ define "main" }}
  {{ .Content }}
  <h2>Cores</h2>
  <ul>
    {{ range .Site.Sections }}
      <li><a href="{{ .RelPermalink }}">{{ .Title }}</a> ({{ len .Pages }})</li>
    {{ end }}
  </ul>
{{ end }}
```

- [ ] **Step 6: Commit**

```bash
git add mai/mai-data/hugo.toml mai/mai-data/layouts/
git commit -m "feat: minimal self-contained Hugo scaffolding (config + layouts)"
```

---

### Task 5: CLI repoint + full-suite green

**Files:**
- Modify: `mai/src/mai/cli/__main__.py` (repoint `_publish` to `publish_site`)

- [ ] **Step 1: Repoint `_publish` in `cli/__main__.py`**

Replace the body of the existing `_publish` coroutine so it delegates to `publish_site` (writing into the ledger dir). The new body:

```python
async def _publish() -> int:
    from mai.publish.site import publish_site

    async with SessionFactory() as session:
        return await publish_site(session, settings.ledger_path)
```

Remove any now-unused imports in `cli/__main__.py` that were only used by the old `_publish` body (e.g. `report_to_markdown`, `Report`, `ReportSourceMap`, `select`, `Path` — only remove ones that become unused; keep anything still referenced by other subcommands). Leave the `publish` subcommand registration and its dispatch (`print(f"published {count} reports")`) as-is, but if the printed wording references "reports", change it to `print(f"published {count} pages")`.

- [ ] **Step 2: Run the full test suite**

Run: `cd mai && pytest -q`
Expected: PASS (85 passed — 75 prior + 10 new).

- [ ] **Step 3: Smoke-test publish end to end (offline, no keys)**

Run:
```bash
cd mai && rm -f mai.db && python -m mai.cli.__main__ init-db && python -c "
import asyncio
from mai.db.session import SessionFactory
from mai.contracts import IntakeEvent
from mai.ingest import ingest_event
async def go():
    async with SessionFactory() as s:
        await ingest_event(s, IntakeEvent('ips','r1','Pet bug','zero',raw_payload={'markdown':'x'}))
        await s.commit()
asyncio.run(go())
" && python -m mai.cli.__main__ publish && cat mai-data/content/_index.md && echo "---" && cat mai-data/content/zero/bugs/ips-r1.md
```
Expected: prints `published N pages`, then the home `_index.md` (with counts) and the bug page (with front-matter + `# Pet bug`).

- [ ] **Step 4: (Optional, needs Hugo installed) Build the site**

Run: `cd mai/mai-data && hugo --quiet && ls public/index.html`
Expected: builds to `public/`. If `hugo` is not installed, SKIP and note it — the content generation is already covered by `test_publish_site.py`.

- [ ] **Step 5: Commit**

```bash
git add mai/src/mai/cli/__main__.py
git commit -m "feat: mai CLI publish renders the full Hugo content tree"
```

---

## Self-Review

- **Spec coverage:** Implements the publish/projection of spec §5 (single Hugo site, per-core sections, sync view) and §7 stage 4 — bug pages carry their verdict + evidence; drift pages carry the subsystem matrix; home shows the dashboard counts. Offline, no infra.
- **Invariants:** deterministic projection of the store (re-runnable) ✓ · reads via views/repository seam ✓ · front-matter is a versioned (`schema_version: 2`) public contract ✓ · derived (writes only the site tree, never the DB) ✓.
- **Placeholder scan:** none — every step has runnable code/commands.
- **Type consistency:** `ReportBundle` (from `views`) is consumed by `render_report_page`; `drift_observations_by_pair` returns `{(fork_a,fork_b): [DriftObservation]}` consumed by `publish_site` + `render_drift_page`; `counts` keys (`reports/enriched/open/likely_fixed/fixed_confirmed/drift_pairs`) match `render_home`.

## Notes for later plans

- **Stale-file cleanup:** `publish_site` overwrites but doesn't delete pages for reports that disappeared; add a clean-or-prune pass (or write to a fresh dir + swap) before the first incremental publish.
- **Per-core `_index.md`:** Hugo auto-lists sections; a generated per-core index with counts/verdict breakdown would be a nice enhancement.
- **Deploy (Plan 09):** Cloudflare Pages builds this `mai-data/` on push; Access gates it; the only code change is none — publish already targets the ledger dir.
- **Theme:** the layouts are intentionally minimal; a richer theme (filtering, drift heatmap colors) is a later polish.
- **Plan 01 `report_to_markdown` (v1) is now superseded** by `render_report_page` (v2); it and its test can be removed in a cleanup pass.
