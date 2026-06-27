import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mai.db.base import Base
from mai.db.models import ReviewAdvice


@pytest_asyncio.fixture
async def cache_session():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(eng, expire_on_commit=False)() as s:
        yield s


async def test_review_advice_row_roundtrips(cache_session):
    cache_session.add(ReviewAdvice(
        patch_group_id="pg1", core="four", source_sha="s1", base_sha="b1",
        model="anthropic/claude-sonnet-4.6", prompt_version=1, assessment="divergent",
        confidence=0.6, reason="x", tips=["t"], citations=["c"], adapted_hunks=[], grounded=True))
    await cache_session.commit()
    row = await cache_session.scalar(select(ReviewAdvice).where(
        ReviewAdvice.patch_group_id == "pg1", ReviewAdvice.core == "four"))
    assert row.assessment == "divergent" and row.confidence == 0.6
    assert row.tips == ["t"] and row.grounded is True
