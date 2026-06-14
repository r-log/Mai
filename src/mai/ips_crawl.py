from sqlalchemy.ext.asyncio import AsyncSession

from mai.ingest import ingest_event
from mai.ips.client import IpsClient
from mai.ips.normalize import normalize_ips


async def crawl_all(session: AsyncSession, client: IpsClient) -> int:
    """Discover all bug URLs, fetch + ingest each. Commits per bug (resumable)."""
    urls = await client.list_bug_urls()
    count = 0
    for url in urls:
        markdown = await client.fetch_bug(url)
        await ingest_event(session, normalize_ips(url, markdown))
        await session.commit()
        count += 1
    return count
