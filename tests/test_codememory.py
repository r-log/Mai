import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mai.db.base import Base
from mai.db.models import CodeFileIndex


@pytest_asyncio.fixture
async def cm_session():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(eng, expire_on_commit=False)() as s:
        yield s


async def test_code_file_index_roundtrips(cm_session):
    cm_session.add(CodeFileIndex(
        core="zero", base_sha="b1", path="src/x.cpp", exists=True,
        file_symbols=["A", "B"],
        functions=[{"name": "f", "qualified_name": "C::f", "start_line": 1,
                    "end_line": 9, "params": ["x"], "locals": ["y"]}]))
    await cm_session.commit()
    row = await cm_session.scalar(select(CodeFileIndex).where(
        CodeFileIndex.core == "zero", CodeFileIndex.base_sha == "b1",
        CodeFileIndex.path == "src/x.cpp"))
    assert row.exists is True and row.file_symbols == ["A", "B"]
    assert row.functions[0]["name"] == "f" and row.functions[0]["params"] == ["x"]
