"""build_me_dashboard: My todo / Shipped / Available + team + project, under the
one-action model (engine auto-resolves; humans only take)."""
from datetime import datetime, timezone

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mai.db.base import Base
from mai.db.models import (BoardEvent, BoardItem, Commit, PatchGroup, PortVerdict)
from mai.publish.me_dashboard import build_me_dashboard


def _verdict(pg, core, verdict, sha="s0"):
    return PortVerdict(patch_group_id=pg, core=core, verdict=verdict,
                       apply_result="clean", relevance="portable", source_core="three",
                       source_sha=sha, subsystem="src/shared/Database", magnitude=10,
                       tier="surgical")


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        for p in ("pg1", "pg2", "pg3"):
            s.add(PatchGroup(id=p, patch_id="pid-" + p))
        ts = datetime(2026, 6, 1, tzinfo=timezone.utc)
        s.add(Commit(core="three", sha="s0", author="a", authored_at=ts,
                     committer="a", committed_at=ts, message="db crash fix on shutdown"))
        # pg1:two needs (dev took, active); pg1:four shipped (has_it + archived);
        # pg2:two needs (assigned to dev, active); pg3:two needs (free)
        s.add(_verdict("pg1", "two", "needs"))
        s.add(_verdict("pg1", "four", "has_it"))
        s.add(_verdict("pg2", "two", "needs"))
        s.add(_verdict("pg3", "two", "needs"))
        s.add(BoardItem(port_candidate_id="pg1:two", assignee="dev", status="claimed",
                        updated_by="dev"))
        s.add(BoardItem(port_candidate_id="pg1:four", assignee="dev", status="claimed",
                        archived=True, updated_by="dev"))
        s.add(BoardItem(port_candidate_id="pg2:two", assignee="dev", status="claimed",
                        updated_by="antz"))
        s.add(BoardEvent(port_candidate_id="pg1:two", seq=1, actor="dev", action="claim"))
        s.add(BoardEvent(port_candidate_id="pg1:four", seq=1, actor="dev", action="claim"))
        s.add(BoardEvent(port_candidate_id="pg2:two", seq=1, actor="antz",
                         action="assign", to_value="dev"))
        await s.commit()
        yield s


async def test_dashboard_stats_and_lanes(session):
    d = await build_me_dashboard(session, "dev")
    assert d["stats"] == {"todo": 2, "shipped": 1, "available": 1, "self": 1, "assigned": 1}
    assert len(d["queue"]) == 2
    assert {q["item_id"] for q in d["queue"]} == {"pg1:two", "pg2:two"}
    assert {q["via"] for q in d["queue"]} == {"self", "assigned"}
    assert d["queue"][0]["title"] == "db crash fix on shutdown"   # resolved from Commit
    assert len(d["shipped"]) == 1 and d["shipped"][0]["core"] == "four"


async def test_dashboard_team_and_project(session):
    d = await build_me_dashboard(session, "dev")
    assert d["project"] == {"needs_total": 3, "needs_claimed": 2, "unclaimed": 1}
    dev = next(t for t in d["team"] if t["user"] == "dev")
    assert dev == {"user": "dev", "todo": 2, "shipped": 1}


async def test_activity_is_only_my_events(session):
    d = await build_me_dashboard(session, "dev")
    # the antz->dev assign was authored by antz, so it is NOT in dev's activity
    assert {a["action"] for a in d["activity"]} == {"claim"}
    assert len(d["activity"]) == 2
    assert len(d["spark"]) == 14
