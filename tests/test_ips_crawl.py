from pathlib import Path

from sqlalchemy import func, select

from mai.db.models import Report, SourceRecord
from mai.ips.fake import FakeIpsClient
from mai.ips_crawl import crawl_all

FIXTURE = (Path(__file__).parent / "fixtures" / "ips_bug_r1842.md").read_text(encoding="utf-8")
URL1 = ("https://www.getmangos.eu/bug-tracker/mangos-zero/"
        "agro-from-pet-doesnt-work-as-expected-r1842/")
URL2 = "https://www.getmangos.eu/bug-tracker/mangos-three/night-elf-1-10-r1861/"
PAGE2 = "# Night Elf 1 - 10\n\nStatus: New\n\n**Main Category:** Core\n"


async def test_crawl_all_ingests_each_bug(session):
    client = FakeIpsClient(urls=[URL1, URL2], pages={URL1: FIXTURE, URL2: PAGE2})
    n = await crawl_all(session, client)
    assert n == 2
    assert await session.scalar(select(func.count()).select_from(SourceRecord)) == 2
    keys = set(await session.scalars(select(Report.canonical_key)))
    assert keys == {"ips:r1842", "ips:r1861"}


async def test_crawl_all_is_idempotent(session):
    client = FakeIpsClient(urls=[URL1, URL2], pages={URL1: FIXTURE, URL2: PAGE2})
    await crawl_all(session, client)
    await crawl_all(session, client)
    assert await session.scalar(select(func.count()).select_from(SourceRecord)) == 2
