from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from mai.publish.dataviz import write_dataviz
from mai.publish.render import render_drift_page, render_home, render_report_page
from mai.publish.slug import safe_slug
from mai.publish.views import (
    counts, drift_observations_by_pair, iter_bug_reports, report_bundle,
)


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
        (target / f"{safe_slug(report.canonical_key)}.md").write_text(
            render_report_page(bundle), encoding="utf-8")
        written += 1

    pairs = await drift_observations_by_pair(session)
    if pairs:
        sync = content / "sync"
        sync.mkdir(parents=True, exist_ok=True)
        for (fork_a, fork_b), observations in pairs.items():
            slug = f"{safe_slug(fork_a)}--vs--{safe_slug(fork_b)}"
            (sync / f"{slug}.md").write_text(
                render_drift_page(fork_a, fork_b, observations), encoding="utf-8")
            written += 1

    await write_dataviz(session, out_dir)
    return written
