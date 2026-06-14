import argparse
import asyncio
from pathlib import Path

from mai.config import settings
from mai.db.base import Base
from mai.db.session import SessionFactory, engine
from mai.publish.markdown import report_to_markdown
from mai.repository.reports import ReportRepository


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


def main() -> None:
    parser = argparse.ArgumentParser(prog="mai")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db")
    sub.add_parser("publish")
    args = parser.parse_args()

    if args.cmd == "init-db":
        asyncio.run(_init_db())
        print("db initialized")
    elif args.cmd == "publish":
        count = asyncio.run(_publish())
        print(f"published {count} reports")


if __name__ == "__main__":
    main()
