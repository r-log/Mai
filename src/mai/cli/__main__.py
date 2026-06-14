import argparse
import asyncio
from pathlib import Path

from mai.config import settings
from mai.db.base import Base
from mai.db.session import SessionFactory, engine
from mai.publish.markdown import report_to_markdown
from mai.repository.reports import ReportRepository
from mai.repository.repos import RepoRepository
from mai.sources.registry import parse_registry


async def _init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _publish() -> int:
    out = Path(settings.ledger_path) / "content"
    async with SessionFactory() as session:
        repo = ReportRepository(session)
        reports = await repo.all_reports()
        for report in reports:
            keys = await repo.source_keys_for(report.id)
            target = out / report.core / "bugs"
            target.mkdir(parents=True, exist_ok=True)
            (target / f"{report.canonical_key.replace(':', '-')}.md").write_text(
                report_to_markdown(report, keys), encoding="utf-8"
            )
    return len(reports)


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


def main() -> None:
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


if __name__ == "__main__":
    main()
