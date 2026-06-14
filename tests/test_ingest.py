from sqlalchemy import func, select

from mai.contracts import IntakeEvent
from mai.db.models import Event, Report, ReportSourceMap, SourceRecord
from mai.ingest import ingest_event

EVT = IntakeEvent(
    source_type="ips", source_id="r1842",
    title="Agro from pet doesnt work", core="zero",
    status="open", raw_payload={"body": "threat union bug"},
)


async def test_ingest_creates_raw_report_map_and_event(session):
    await ingest_event(session, EVT)
    await session.commit()
    assert await session.scalar(select(func.count()).select_from(SourceRecord)) == 1
    assert await session.scalar(select(func.count()).select_from(Report)) == 1
    assert await session.scalar(select(func.count()).select_from(ReportSourceMap)) == 1
    assert await session.scalar(select(func.count()).select_from(Event)) == 1
    report = await session.scalar(select(Report))
    assert report.canonical_key == "ips:r1842"


async def test_ingest_is_idempotent_on_identical_payload(session):
    await ingest_event(session, EVT)
    await ingest_event(session, EVT)
    await session.commit()
    assert await session.scalar(select(func.count()).select_from(SourceRecord)) == 1
    assert await session.scalar(select(func.count()).select_from(Report)) == 1


async def test_ingest_appends_new_version_on_changed_payload(session):
    await ingest_event(session, EVT)
    changed = IntakeEvent(
        source_type="ips", source_id="r1842", title="Agro from pet doesnt work",
        core="zero", status="completed", raw_payload={"body": "EDITED"},
    )
    await ingest_event(session, changed)
    await session.commit()
    assert await session.scalar(select(func.count()).select_from(SourceRecord)) == 2
    assert await session.scalar(select(func.count()).select_from(Report)) == 1  # same report
    report = await session.scalar(select(Report))
    assert report.status == "completed"
