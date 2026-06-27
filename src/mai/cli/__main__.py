import argparse
import asyncio
from pathlib import Path

from mai.config import settings
from mai.db.base import Base
from mai.db.session import SessionFactory, engine
from mai.repository.repos import RepoRepository
from mai.sources.registry import parse_registry


async def _init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _publish() -> int:
    from mai.publish.site import publish_site

    async with SessionFactory() as session:
        return await publish_site(session, settings.ledger_path)


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


async def _enrich() -> int:
    if not settings.openrouter_api_key:
        raise SystemExit("OPENROUTER_API_KEY not set")
    import httpx

    from mai.enrich.enricher import OpenRouterEnricher
    from mai.enrich_run import enrich_pending_concurrent

    async with httpx.AsyncClient(timeout=120.0) as http:
        enricher = OpenRouterEnricher(
            settings.openrouter_api_key,
            settings.enrichment_model,
            base_url=settings.openrouter_api_url,
            client=http,
        )
        async with SessionFactory() as session:
            return await enrich_pending_concurrent(
                session, enricher, concurrency=settings.enrichment_concurrency)


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


async def _correlate() -> dict:
    from mai.correlate.run import correlate_all

    async with SessionFactory() as session:
        return await correlate_all(session, settings.embedding_model)


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


async def _commits_harvest() -> int:
    from mai.git.client import LocalGitClient
    from mai.git_harvest import commits_harvest_repo

    client = LocalGitClient(settings.git_mirror_dir)
    async with SessionFactory() as session:
        repos = await RepoRepository(session).all()
        total = 0
        for repo in repos:
            total += await commits_harvest_repo(session, client, repo)
            await session.commit()
    return total


async def _warm_advice(limit: int, concurrency: int):
    from mai.db.session import SessionFactory
    from mai.git.client import LocalGitClient
    from mai.orchestrate.warm import warm_advice
    if not settings.openrouter_api_key:
        return None
    from mai.judge.judge import OpenRouterJudge
    judge = OpenRouterJudge(settings.openrouter_api_key, settings.openrouter_api_url)
    git = LocalGitClient(settings.git_mirror_dir, settings.git_worktree_dir)
    return await warm_advice(SessionFactory, git, judge, limit=limit, concurrency=concurrency)


async def _sync_analyze() -> dict:
    from mai.git.client import LocalGitClient
    from mai.sync.classify import classify_subsystems
    from mai.sync.portcandidates import compute_port_candidates
    from mai.sync.propagate import compute_propagation
    from mai.sync.verdicts import compute_verdicts

    async with SessionFactory() as session:
        propagation = await compute_propagation(session)
        classification = await classify_subsystems(session)
        port_candidates = await compute_port_candidates(session)
        verdicts = await compute_verdicts(session, LocalGitClient(settings.git_mirror_dir))
        return {"propagation": propagation, "classification": classification,
                "port_candidates": port_candidates, "verdicts": verdicts}


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


async def _user_add(username: str, is_maintainer: bool) -> str:
    from mai.auth.accounts import create_account
    from mai.auth.hasher import Argon2Hasher

    async with SessionFactory() as session:
        password = await create_account(session, Argon2Hasher(), username,
                                        is_maintainer=is_maintainer)
        await session.commit()
    return password


async def _user_list() -> list:
    from mai.repository.users import UserRepository

    async with SessionFactory() as session:
        return await UserRepository(session).all()


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
    wa = sub.add_parser("warm-advice")
    wa.add_argument("--limit", type=int, default=200)
    wa.add_argument("--concurrency", type=int, default=4)
    sub.add_parser("refresh")
    sub.add_parser("serve")
    ua = sub.add_parser("user-add")
    ua.add_argument("username")
    ua.add_argument("--maintainer", action="store_true")
    sub.add_parser("user-list")
    sub.add_parser("serve-web")
    return parser


def main() -> None:
    args = build_parser().parse_args()

    if args.cmd == "init-db":
        asyncio.run(_init_db())
        print("db initialized")
    elif args.cmd == "publish":
        count = asyncio.run(_publish())
        print(f"published {count} pages")
    elif args.cmd == "registry-load":
        count = asyncio.run(_registry_load(args.readme_path))
        print(f"loaded {count} repos")
    elif args.cmd == "harvest":
        count = asyncio.run(_harvest())
        print(f"harvested {count} repos")
    elif args.cmd == "ips-crawl":
        count = asyncio.run(_ips_crawl())
        print(f"crawled {count} bugs")
    elif args.cmd == "enrich":
        count = asyncio.run(_enrich())
        print(f"enriched {count} reports")
    elif args.cmd == "embed":
        count = asyncio.run(_embed())
        print(f"embedded {count} reports")
    elif args.cmd == "correlate":
        result = asyncio.run(_correlate())
        print(f"correlate: explicit={result['explicit_edges']} "
              f"embedding={result['embedding_edges']} verified={result['verified']}")
    elif args.cmd == "drift":
        rows = asyncio.run(_drift())
        print(f"drift: {rows} subsystem observations")
    elif args.cmd == "commits-harvest":
        count = asyncio.run(_commits_harvest())
        print(f"commits-harvest: {count} new commits")
    elif args.cmd == "sync-analyze":
        result = asyncio.run(_sync_analyze())
        p, c, pc = (result["propagation"], result["classification"],
                    result["port_candidates"])
        t = pc["tiers"]
        print(f"sync-analyze: groups={p['groups']} present={p['present']} "
              f"absent={p['absent']} cherry_links={p['cherry_links']} | "
              f"subsystems={c['total']} shared={c['shared']} "
              f"client_bound={c['client_bound']}(+{c.get('client_bound_from_drift', 0)} drift) "
              f"expansion={c['expansion']} mixed={c['mixed']} vendored={c['vendored']} | "
              f"port_candidates={pc['candidates']} "
              f"(surgical={t['surgical']} small={t['small']} moderate={t['moderate']} "
              f"bulk={t['bulk']}) skipped={pc['skipped_unportable']} "
              f"resolved={pc['auto_resolved']}")
        v = result["verdicts"]
        print(f"verdicts: needs={v['needs']} review={v['review']} "
              f"n/a={v['not_applicable']} has_it={v['has_it']} "
              f"(recomputed={v['recomputed']} cached={v['cached']})")
    elif args.cmd == "warm-advice":
        result = asyncio.run(_warm_advice(args.limit, args.concurrency))
        if result is None:
            print("warm-advice: no OPENROUTER_API_KEY set — nothing warmed")
        else:
            print(f"warm-advice: planned={result['planned']} "
                  f"warmed={result['warmed']} failed={result['failed']}")
    elif args.cmd == "refresh":
        result = asyncio.run(_refresh())
        print(f"refresh: +{result.new_commits} commits, "
              f"{result.harvested_repos} repos harvested, "
              f"{result.port_candidates} port candidates, {result.pages} pages")
    elif args.cmd == "serve":
        print(f"serving: refresh every {settings.refresh_interval_seconds}s "
              "(Ctrl-C to stop)")
        asyncio.run(_serve())
    elif args.cmd == "user-add":
        try:
            password = asyncio.run(_user_add(args.username, args.maintainer))
        except ValueError as exc:
            raise SystemExit(str(exc))
        print(f"created user '{args.username}'"
              f"{' (maintainer)' if args.maintainer else ''}")
        print(f"one-time password (give to the user privately, they must change it "
              f"on first login):\n    {password}")
    elif args.cmd == "user-list":
        users = asyncio.run(_user_list())
        for u in users:
            flags = []
            if u.is_maintainer:
                flags.append("maintainer")
            if u.must_change_password:
                flags.append("must-change-pw")
            suffix = f"  [{', '.join(flags)}]" if flags else ""
            print(f"{u.username}{suffix}")
        print(f"{len(users)} user(s)")
    elif args.cmd == "serve-web":
        import uvicorn

        from mai.web.asgi import build_app
        if settings.cookie_secure and settings.session_secret == "dev-insecure-change-me":
            raise SystemExit(
                "refusing to serve with the default SESSION_SECRET while "
                "COOKIE_SECURE is on.\nSet a real SESSION_SECRET in .env for "
                "production, or COOKIE_SECURE=false for local http dev.")
        print(f"serving web app on {settings.web_host}:{settings.web_port} "
              "(Ctrl-C to stop)")
        uvicorn.run(build_app(), host=settings.web_host, port=settings.web_port,
                    proxy_headers=True, forwarded_allow_ips=settings.forwarded_allow_ips)


if __name__ == "__main__":
    main()
