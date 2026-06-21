# Multi-User Board — Phase A: Self-Freshness + Live Deploy — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn today's manual CLI chain into one idempotent `run_refresh_cycle()` driven by a resilient cron loop, so the port-debt site stays truthful on its own — then deploy it to a real always-on URL.

**Architecture:** A new `mai.refresh` package orchestrates the existing, already-tested stages (commits-harvest → PR-harvest → sync-analyze → publish) behind a single function, fires an optional deploy hook to rebuild the live site, and runs on a `CronTrigger` loop that survives a failing cycle. All logic is exercised with the existing `Fake*` seams; the GitHub-App webhook accelerator is a later phase (C). Deployment (box, Neon, Cloudflare Pages/Access) is a manual runbook with verification gates.

**Tech Stack:** Python 3.12, async SQLAlchemy 2.0, pydantic-settings, pytest + pytest-asyncio. Vanilla CLI (argparse). No new runtime dependencies.

## Global Constraints

- **Python 3.12**, async SQLAlchemy 2.0, httpx, pydantic-settings, pytest. Match existing `Fake*` protocol seams + the repository seam (SQLite local → Neon deploy).
- **4-space indent**, no tabs.
- **Commit style:** `feat:` / `docs:` / `test:` prefix, terse. **No AI attribution** — no `Co-Authored-By`, no "Generated with" footer.
- **Read-only externally.** The refresh cycle only reads GitHub/git and writes Mai's own DB + local `mai-data/`. No write-back to GitHub/IPS.
- **Idempotent & cursor-gated.** Every stage already advances its own cursor; the cycle must be safe to run repeatedly (a no-op second run when nothing changed).
- **Engine owns truth.** The cycle recomputes port-debt from code; it never sets a fix "ported" by hand.
- Tests live in `tests/`; run with `python -m pytest`. The `session` fixture (in `tests/conftest.py`) gives an in-memory async SQLite session with all tables created.

---

## File Structure

| File | Responsibility |
|------|----------------|
| `src/mai/refresh/__init__.py` | Package marker (empty). |
| `src/mai/refresh/deploy.py` | `DeployHook` protocol + `ShellDeployHook` (runs a configured shell command to rebuild the live site). |
| `src/mai/refresh/cycle.py` | `RefreshResult` dataclass + `run_refresh_cycle()` — orchestrates the existing stages + optional deploy. |
| `src/mai/refresh/trigger.py` | `Clock` protocol + `RealClock` + `run_cron()` resilient loop. |
| `src/mai/refresh/fake.py` | `FakeClock`, `FakeDeployHook` — test seams (mirrors `mai.git.fake`/`mai.github.fake`). |
| `src/mai/config.py` (modify) | Add `refresh_interval_seconds`, `deploy_command`. |
| `src/mai/cli/__main__.py` (modify) | Extract `build_parser()`; add `refresh` + `serve` subcommands. |
| `tests/test_deploy_hook.py` | `ShellDeployHook` success + failure. |
| `tests/test_refresh_cycle.py` | Orchestration: stages run, idempotent, github=None skip, deploy fired. |
| `tests/test_refresh_trigger.py` | `run_cron` runs N times + survives a failing cycle. |
| `tests/test_cli_parser.py` | `build_parser()` accepts `refresh`/`serve`. |
| `docs/runbooks/phase-a-deploy.md` | Manual deployment runbook (Task 5). |

---

## Task 1: DeployHook seam

**Files:**
- Create: `src/mai/refresh/__init__.py`
- Create: `src/mai/refresh/deploy.py`
- Create: `src/mai/refresh/fake.py`
- Modify: `src/mai/config.py`
- Test: `tests/test_deploy_hook.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `DeployHook` protocol (`async def trigger(self) -> None`); `ShellDeployHook(command: str)`; `FakeDeployHook()` with `.calls: int`; `settings.deploy_command: str | None`.

- [ ] **Step 1: Create the empty package marker**

Create `src/mai/refresh/__init__.py` with no content (empty file).

- [ ] **Step 2: Write the failing test**

Create `tests/test_deploy_hook.py`:

```python
import asyncio

import pytest

from mai.refresh.deploy import ShellDeployHook


class _Proc:
    def __init__(self, rc: int) -> None:
        self._rc = rc

    async def wait(self) -> int:
        return self._rc


async def test_shell_deploy_hook_runs_command(monkeypatch):
    seen = []

    async def fake_shell(cmd):
        seen.append(cmd)
        return _Proc(0)

    monkeypatch.setattr(
        "mai.refresh.deploy.asyncio.create_subprocess_shell", fake_shell)
    await ShellDeployHook("deploy.sh").trigger()
    assert seen == ["deploy.sh"]


async def test_shell_deploy_hook_raises_on_failure(monkeypatch):
    async def fake_shell(cmd):
        return _Proc(2)

    monkeypatch.setattr(
        "mai.refresh.deploy.asyncio.create_subprocess_shell", fake_shell)
    with pytest.raises(RuntimeError):
        await ShellDeployHook("deploy.sh").trigger()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_deploy_hook.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mai.refresh.deploy'`.

- [ ] **Step 4: Write the implementation**

Create `src/mai/refresh/deploy.py`:

```python
import asyncio
from typing import Protocol


class DeployHook(Protocol):
    """Rebuilds/publishes the live site after a refresh."""

    async def trigger(self) -> None: ...


class ShellDeployHook:
    """Runs a configured shell command (e.g. a build+upload script)."""

    def __init__(self, command: str) -> None:
        self._command = command

    async def trigger(self) -> None:
        proc = await asyncio.create_subprocess_shell(self._command)
        rc = await proc.wait()
        if rc != 0:
            raise RuntimeError(
                f"deploy command failed (exit {rc}): {self._command}")
```

Create `src/mai/refresh/fake.py`:

```python
class FakeClock:
    """Records requested sleeps instead of waiting."""

    def __init__(self) -> None:
        self.sleeps: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


class FakeDeployHook:
    """Counts deploy triggers."""

    def __init__(self) -> None:
        self.calls = 0

    async def trigger(self) -> None:
        self.calls += 1
```

- [ ] **Step 5: Add config fields**

In `src/mai/config.py`, add these two lines inside the `Settings` class, right after `git_mirror_dir: str = "./mirrors"`:

```python
    refresh_interval_seconds: int = 10800
    deploy_command: str | None = None
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_deploy_hook.py -v`
Expected: PASS (2 passed).

- [ ] **Step 7: Commit**

```bash
git add src/mai/refresh/__init__.py src/mai/refresh/deploy.py src/mai/refresh/fake.py src/mai/config.py tests/test_deploy_hook.py
git commit -m "feat: deploy hook seam + refresh config fields"
```

---

## Task 2: run_refresh_cycle orchestration

**Files:**
- Create: `src/mai/refresh/cycle.py`
- Test: `tests/test_refresh_cycle.py`

**Interfaces:**
- Consumes: `RepoRepository(session).all()`; `commits_harvest_repo(session, git_client, repo) -> int`; `harvest_repo(session, github_client, repo)`; `compute_propagation(session)`; `classify_subsystems(session)`; `compute_port_candidates(session) -> dict` (has key `"candidates": int`); `publish_site(session, ledger_path) -> int`; `DeployHook` (Task 1).
- Produces: `RefreshResult(new_commits: int, harvested_repos: int, port_candidates: int, pages: int)`; `async def run_refresh_cycle(session, *, git_client, github_client=None, ledger_path: str, deploy_hook=None) -> RefreshResult`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_refresh_cycle.py`:

```python
from sqlalchemy import func, select

from mai.db.models import Commit, Report, Repo
from mai.git.fake import FakeGitClient
from mai.git.types import CommitFileMeta, CommitMeta
from mai.github.fake import FakeGitHubClient
from mai.refresh.cycle import run_refresh_cycle
from mai.refresh.fake import FakeDeployHook

REPO = Repo(full_name="r-log/server", core="three", url="file:///dev/null")
PULLS = [{"number": 10, "title": "Fix A", "state": "closed",
          "merged_at": "2026-01-03T00:00:00Z",
          "updated_at": "2026-01-03T00:00:00Z"}]


def _c(sha: str) -> CommitMeta:
    return CommitMeta(
        sha=sha, author="d", authored_at="2026-01-01T00:00:00Z",
        committer="d", committed_at="2026-01-01T00:00:00Z", message=sha,
        parents=["x"], is_merge=False, patch_id=f"p-{sha}",
        files=[CommitFileMeta(path="src/a.cpp", change_type="M",
                              added=1, removed=0)])


async def test_cycle_harvests_commits_and_prs_and_publishes(session, tmp_path):
    session.add(REPO)
    await session.commit()
    git = FakeGitClient({"three": [_c("s1"), _c("s2")]})
    gh = FakeGitHubClient(issues={"r-log/server": []},
                          pulls={"r-log/server": list(PULLS)})
    deploy = FakeDeployHook()

    result = await run_refresh_cycle(
        session, git_client=git, github_client=gh,
        ledger_path=str(tmp_path), deploy_hook=deploy)

    assert result.new_commits == 2
    assert result.harvested_repos == 1
    assert deploy.calls == 1
    assert result.pages >= 1
    assert await session.scalar(select(func.count()).select_from(Commit)) == 2
    assert await session.scalar(select(func.count()).select_from(Report)) == 1


async def test_cycle_is_idempotent(session, tmp_path):
    session.add(REPO)
    await session.commit()
    git = FakeGitClient({"three": [_c("s1"), _c("s2")]})
    gh = FakeGitHubClient(issues={"r-log/server": []},
                          pulls={"r-log/server": list(PULLS)})
    await run_refresh_cycle(session, git_client=git, github_client=gh,
                            ledger_path=str(tmp_path))
    again = await run_refresh_cycle(session, git_client=git, github_client=gh,
                                    ledger_path=str(tmp_path))
    assert again.new_commits == 0
    assert await session.scalar(select(func.count()).select_from(Commit)) == 2


async def test_cycle_without_github_skips_pr_harvest(session, tmp_path):
    session.add(REPO)
    await session.commit()
    git = FakeGitClient({"three": [_c("s1")]})

    result = await run_refresh_cycle(
        session, git_client=git, github_client=None, ledger_path=str(tmp_path))

    assert result.harvested_repos == 0
    assert await session.scalar(select(func.count()).select_from(Report)) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_refresh_cycle.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mai.refresh.cycle'`.

- [ ] **Step 3: Write the implementation**

Create `src/mai/refresh/cycle.py`:

```python
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from mai.repository.repos import RepoRepository


@dataclass
class RefreshResult:
    new_commits: int
    harvested_repos: int
    port_candidates: int
    pages: int


async def run_refresh_cycle(
    session: AsyncSession,
    *,
    git_client,
    github_client=None,
    ledger_path: str,
    deploy_hook=None,
) -> RefreshResult:
    """Bring the engine + site up to date in one idempotent pass.

    Stages (each already cursor-gated/idempotent): commits-harvest ->
    PR-harvest -> sync-analyze -> publish, then an optional deploy.
    """
    from mai.git_harvest import commits_harvest_repo
    from mai.harvest import harvest_repo
    from mai.publish.site import publish_site
    from mai.sync.classify import classify_subsystems
    from mai.sync.portcandidates import compute_port_candidates
    from mai.sync.propagate import compute_propagation

    repos = await RepoRepository(session).all()

    new_commits = 0
    for repo in repos:
        new_commits += await commits_harvest_repo(session, git_client, repo)
        await session.commit()

    harvested = 0
    if github_client is not None:
        for repo in repos:
            await harvest_repo(session, github_client, repo)
            await session.commit()
            harvested += 1

    await compute_propagation(session)
    await classify_subsystems(session)
    pc = await compute_port_candidates(session)
    await session.commit()

    pages = await publish_site(session, ledger_path)

    if deploy_hook is not None:
        await deploy_hook.trigger()

    return RefreshResult(
        new_commits=new_commits,
        harvested_repos=harvested,
        port_candidates=pc["candidates"],
        pages=pages,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_refresh_cycle.py -v`
Expected: PASS (3 passed). If `result.pages >= 1` fails, read `publish_site` to confirm it always writes the home page; adjust the assert to the real floor only if the home page is conditional.

- [ ] **Step 5: Commit**

```bash
git add src/mai/refresh/cycle.py tests/test_refresh_cycle.py
git commit -m "feat: run_refresh_cycle orchestrates harvest->sync->publish->deploy"
```

---

## Task 3: Resilient cron loop

**Files:**
- Create: `src/mai/refresh/trigger.py`
- Test: `tests/test_refresh_trigger.py`

**Interfaces:**
- Consumes: `FakeClock` (Task 1, in `mai.refresh.fake`).
- Produces: `Clock` protocol (`async def sleep(self, seconds: float) -> None`); `RealClock`; `async def run_cron(cycle, *, interval_seconds, clock, max_runs=None) -> int`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_refresh_trigger.py`:

```python
from mai.refresh.fake import FakeClock
from mai.refresh.trigger import run_cron


async def test_run_cron_runs_cycle_n_times():
    calls = []

    async def cycle():
        calls.append(1)

    clock = FakeClock()
    runs = await run_cron(cycle, interval_seconds=5, clock=clock, max_runs=3)
    assert runs == 3
    assert len(calls) == 3
    assert clock.sleeps == [5, 5]  # sleeps between runs, none after the last


async def test_run_cron_survives_a_failing_cycle():
    calls = []

    async def cycle():
        calls.append(1)
        raise RuntimeError("boom")

    clock = FakeClock()
    runs = await run_cron(cycle, interval_seconds=1, clock=clock, max_runs=2)
    assert runs == 2
    assert len(calls) == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_refresh_trigger.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mai.refresh.trigger'`.

- [ ] **Step 3: Write the implementation**

Create `src/mai/refresh/trigger.py`:

```python
import asyncio
import logging
from typing import Awaitable, Callable, Protocol

logger = logging.getLogger(__name__)


class Clock(Protocol):
    async def sleep(self, seconds: float) -> None: ...


class RealClock:
    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


async def run_cron(
    cycle: Callable[[], Awaitable[object]],
    *,
    interval_seconds: float,
    clock: Clock,
    max_runs: int | None = None,
) -> int:
    """Call cycle() forever (or max_runs times), sleeping between runs.

    A failing cycle is logged and swallowed so the backstop never dies.
    """
    runs = 0
    while max_runs is None or runs < max_runs:
        try:
            await cycle()
        except Exception:  # noqa: BLE001 - cron must survive a failed cycle
            logger.exception("refresh cycle failed; continuing")
        runs += 1
        if max_runs is not None and runs >= max_runs:
            break
        await clock.sleep(interval_seconds)
    return runs
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_refresh_trigger.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add src/mai/refresh/trigger.py tests/test_refresh_trigger.py
git commit -m "feat: resilient cron loop for refresh backstop"
```

---

## Task 4: CLI `refresh` + `serve`

**Files:**
- Modify: `src/mai/cli/__main__.py`
- Test: `tests/test_cli_parser.py`

**Interfaces:**
- Consumes: `run_refresh_cycle` (Task 2), `run_cron`/`RealClock` (Task 3), `ShellDeployHook` (Task 1), `settings.refresh_interval_seconds`/`settings.deploy_command` (Task 1).
- Produces: `build_parser() -> argparse.ArgumentParser`; CLI commands `refresh`, `serve`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli_parser.py`:

```python
from mai.cli.__main__ import build_parser


def test_parser_accepts_refresh():
    assert build_parser().parse_args(["refresh"]).cmd == "refresh"


def test_parser_accepts_serve():
    assert build_parser().parse_args(["serve"]).cmd == "serve"


def test_parser_still_accepts_existing_command():
    assert build_parser().parse_args(["sync-analyze"]).cmd == "sync-analyze"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli_parser.py -v`
Expected: FAIL with `ImportError: cannot import name 'build_parser'`.

- [ ] **Step 3: Extract `build_parser()` and add the new commands**

In `src/mai/cli/__main__.py`, replace the body of `main()` from `parser = argparse.ArgumentParser(prog="mai")` down to (and including) the line `args = parser.parse_args()` with a call to a new extracted function. First add this new function directly above `def main() -> None:`:

```python
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mai")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db")
    sub.add_parser("publish")
    rl = sub.add_parser("registry-load")
    rl.add_argument("readme_path")
    sub.add_parser("harvest")
    sub.add_parser("ips-crawl")
    sub.add_parser("enrich")
    sub.add_parser("embed")
    sub.add_parser("correlate")
    sub.add_parser("drift")
    sub.add_parser("commits-harvest")
    sub.add_parser("sync-analyze")
    sub.add_parser("refresh")
    sub.add_parser("serve")
    return parser
```

Then change the top of `main()` so it starts with:

```python
def main() -> None:
    args = build_parser().parse_args()
```

(Delete the old inline parser construction that previously lived in `main()`.)

- [ ] **Step 4: Add the `_refresh` and `_serve` coroutines**

In `src/mai/cli/__main__.py`, add these two coroutines next to the other `_*` helpers (e.g. directly above `def build_parser`):

```python
async def _refresh() -> "object":
    from mai.git.client import LocalGitClient
    from mai.refresh.cycle import run_refresh_cycle
    from mai.refresh.deploy import ShellDeployHook

    git_client = LocalGitClient(settings.git_mirror_dir)
    deploy_hook = (ShellDeployHook(settings.deploy_command)
                   if settings.deploy_command else None)
    http = None
    github_client = None
    if settings.github_token:
        import httpx

        from mai.github.client import HttpGitHubClient
        http = httpx.AsyncClient()
        github_client = HttpGitHubClient(
            settings.github_token, base_url=settings.github_api_url, client=http)
    try:
        async with SessionFactory() as session:
            return await run_refresh_cycle(
                session, git_client=git_client, github_client=github_client,
                ledger_path=settings.ledger_path, deploy_hook=deploy_hook)
    finally:
        if http is not None:
            await http.aclose()


async def _serve() -> None:
    from mai.refresh.trigger import RealClock, run_cron

    async def _cycle() -> None:
        result = await _refresh()
        print(f"refresh: +{result.new_commits} commits, "
              f"{result.port_candidates} port candidates, {result.pages} pages")

    await run_cron(_cycle, interval_seconds=settings.refresh_interval_seconds,
                   clock=RealClock())
```

- [ ] **Step 5: Wire the dispatch**

In `main()`, add these two branches to the `if/elif` dispatch chain (after the `sync-analyze` branch, before the function ends):

```python
    elif args.cmd == "refresh":
        result = asyncio.run(_refresh())
        print(f"refresh: +{result.new_commits} commits, "
              f"{result.harvested_repos} repos harvested, "
              f"{result.port_candidates} port candidates, {result.pages} pages")
    elif args.cmd == "serve":
        print(f"serving: refresh every {settings.refresh_interval_seconds}s "
              "(Ctrl-C to stop)")
        asyncio.run(_serve())
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_cli_parser.py -v`
Expected: PASS (3 passed).

- [ ] **Step 7: Run the full suite (no regressions)**

Run: `python -m pytest -q`
Expected: all green (prior 175 + the new Phase A tests, ~184 passed).

- [ ] **Step 8: Smoke the CLI end-to-end locally**

Run (against the local `mai.db`/mirrors on the build host):
```bash
python -m mai.cli init-db
python -m mai.cli refresh
```
Expected: prints a `refresh: +N commits, ... port candidates, ... pages` line and exits 0. (With no mirrors/token yet it should still complete with `+0 commits` and publish the existing data — confirm no traceback.)

- [ ] **Step 9: Commit**

```bash
git add src/mai/cli/__main__.py tests/test_cli_parser.py
git commit -m "feat: mai refresh + serve CLI commands"
```

---

## Task 5: Deployment runbook (MANUAL — no automated test cycle)

> This task is operational: it provisions infrastructure and cannot follow the
> red/green test loop. Its "tests" are the **verification gates** at the end.
> Produce the runbook doc, then execute it once with the owner.

**Files:**
- Create: `docs/runbooks/phase-a-deploy.md`

- [ ] **Step 1: Write the runbook**

Create `docs/runbooks/phase-a-deploy.md` capturing, with exact commands, these sections:

1. **Provision the box** — a small always-on Linux host (VPS or Fly.io machine) with a persistent disk for `mirrors/`. Install: `git`, Python 3.12, the project (`pip install -e .`).
2. **Database (Neon)** — create a Neon Postgres project; set `DATABASE_URL=postgresql+asyncpg://…` in `.env`; run `python -m mai.cli init-db`. (Note: confirm `asyncpg` is installed for the Postgres URL; SQLite stays the local-dev default.)
3. **Secrets (`.env`, gitignored)** — `GITHUB_TOKEN`, `OPENROUTER_API_KEY`, `FIRECRAWL_API_KEY` (as already used), plus `DEPLOY_COMMAND` and `REFRESH_INTERVAL_SECONDS`.
4. **Seed data** — `registry-load <README>` then a first `python -m mai.cli refresh` to populate mirrors + DB + `mai-data/`.
5. **Deploy command** — write `scripts/deploy_site.sh` that builds and publishes the static site, e.g. `hugo -s mai-data && wrangler pages deploy mai-data/public --project-name mai`. Set `DEPLOY_COMMAND="bash scripts/deploy_site.sh"`. (Alternative if using Cloudflare Pages git-integration: the script commits + pushes the regenerated `mai-data/data` and the build runs on Cloudflare.)
6. **Cloudflare Pages + Access** — create the Pages project bound to the site; put the whole site behind Cloudflare Access (dev-only allowlist: r-log, Antz, MadMax) — same posture as the rest of Mai.
7. **Run as a service** — a `systemd` unit (or Fly process) that runs `python -m mai.cli serve` and restarts on failure.

- [ ] **Step 2: Execute the runbook with the owner**

Walk the runbook top to bottom on the real box.

- [ ] **Step 3: Verification gates (the "tests" for this task)**

- [ ] The site loads at its real URL behind Cloudflare Access (only allowlisted users get in).
- [ ] `/port/` shows the four target-fork columns populated from real data.
- [ ] `systemctl status` (or Fly equivalent) shows `mai serve` running.
- [ ] **Freshness proof:** push a trivial commit to a watched fork, wait one `REFRESH_INTERVAL_SECONDS`, and confirm the site's "updated … ago" advances and the new commit is reflected in the data — with **no manual CLI run**.
- [ ] Kill the box once and confirm `systemd`/Fly restarts `serve` (resilience).

- [ ] **Step 4: Commit the runbook**

```bash
git add docs/runbooks/phase-a-deploy.md
git commit -m "docs: phase A deployment runbook"
```

---

## Self-Review

**Spec coverage (Phase A rows of §12 in `port-debt-board-multiuser.md`):**
- "Stand up the backend box (FastAPI + git-worker + cron)" → git-worker already exists (`LocalGitClient`); cron = Tasks 3–4; box = Task 5. *(FastAPI board API is Phase B, correctly out of scope here.)*
- "`Trigger` seam with `CronTrigger` + `run_refresh_cycle()`" → Tasks 2–3.
- "orchestrating the existing stages incrementally" → Task 2 (cursor-gated stages, idempotent test).
- "deploy the static site to a real URL behind Cloudflare Access" → Task 5.
- "Pages rebuild on refresh" → DeployHook (Tasks 1–2) + deploy script (Task 5).
- Outcome "always-on, self-refreshing, all-cores read-only site" → verification gates (Task 5 Step 3).

**Deliberately deferred (documented, not gaps):** the **`WebhookTrigger`** + GitHub App is Phase C; **enrich/embed/correlate/drift** are not in the cycle (cost-gated, not required for port-board truth) — add later config-gated. **Board API / OAuth / BoardItem** are Phase B.

**Placeholder scan:** none — every code step shows complete code; the only non-TDD task (5) is explicitly ops with concrete verification gates.

**Type consistency:** `run_refresh_cycle(session, *, git_client, github_client=None, ledger_path, deploy_hook=None)` and `RefreshResult(new_commits, harvested_repos, port_candidates, pages)` are used identically in Tasks 2 and 4; `DeployHook.trigger()` / `FakeDeployHook.calls` / `FakeClock.sleeps` / `run_cron(..., max_runs=)` match across Tasks 1, 2, 3. `compute_port_candidates(...)["candidates"]` matches the existing CLI's usage.

---

## Execution Handoff

After saving, choose execution mode:
1. **Subagent-Driven (recommended)** — a fresh subagent per task, two-stage review between tasks.
2. **Inline Execution** — execute in this session with checkpoints.
