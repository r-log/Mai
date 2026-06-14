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


def main() -> None:
    parser = argparse.ArgumentParser(prog="mai")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db")
    sub.add_parser("publish")
    rl = sub.add_parser("registry-load")
    rl.add_argument("readme_path")
    sub.add_parser("harvest")
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


if __name__ == "__main__":
    main()
