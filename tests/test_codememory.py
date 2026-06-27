import pytest_asyncio
from sqlalchemy import func, select
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


from mai.cppindex import extract
from mai.cppindex.extract import CppFunction
from mai.git.fake import FakeGitClient
from mai.codememory.index import FileIndex, get_file_index

CPP = (b"namespace ai {\n"
       b"int helper(int a, int b) { int t = a; return t + b; }\n"
       b"class Foo {\n public:\n  void run(char* p) { int q = 0; }\n};\n"
       b"}\n")


def _git_with_file(core, ref, path, content):
    g = FakeGitClient(files={(core, ref, path): content})
    return g


async def test_get_file_index_miss_parses_and_caches(cm_session, monkeypatch):
    calls = {"n": 0}
    real = extract.functions
    def spy(src):
        calls["n"] += 1
        return real(src)
    monkeypatch.setattr("mai.codememory.index.extract.functions", spy)

    git = _git_with_file("zero", "b1", "src/x.cpp", CPP.decode())
    idx1 = await get_file_index(cm_session, git, "zero", "b1", "src/x.cpp")
    assert idx1.exists and "helper" in idx1.file_symbols
    assert idx1.find_function("helper").params == ["a", "b"]
    assert calls["n"] == 1
    # second lookup, same key -> cache hit, NO re-parse
    idx2 = await get_file_index(cm_session, git, "zero", "b1", "src/x.cpp")
    assert idx2.find_function("helper").params == ["a", "b"]
    assert calls["n"] == 1                       # still 1 — served from cache


async def test_get_file_index_reindexes_when_head_moves(cm_session):
    git = FakeGitClient(files={("zero", "b1", "src/x.cpp"): CPP.decode(),
                               ("zero", "b2", "src/x.cpp"): "int only() { return 0; }\n"})
    a = await get_file_index(cm_session, git, "zero", "b1", "src/x.cpp")
    b = await get_file_index(cm_session, git, "zero", "b2", "src/x.cpp")
    assert a.find_function("helper") is not None
    assert b.find_function("helper") is None and b.find_function("only") is not None


async def test_get_file_index_fidelity_matches_extract(cm_session):
    git = _git_with_file("zero", "b1", "src/x.cpp", CPP.decode())
    idx = await get_file_index(cm_session, git, "zero", "b1", "src/x.cpp")
    assert idx.file_symbols == extract.file_symbols(CPP)
    assert idx.find_function("run").params == extract.find_function(CPP, "run").params


async def test_get_file_index_absent_file(cm_session):
    git = FakeGitClient(files={})                # read_file -> None
    idx = await get_file_index(cm_session, git, "zero", "b1", "nope.cpp")
    assert idx.exists is False and idx.file_symbols == set()
    assert idx.find_function("anything") is None


async def test_get_file_index_no_session_parses_directly(cm_session):
    git = _git_with_file("zero", "b1", "src/x.cpp", CPP.decode())
    idx = await get_file_index(None, git, "zero", "b1", "src/x.cpp")   # no cache
    assert idx.find_function("helper").params == ["a", "b"]
    n = await cm_session.scalar(select(func.count()).select_from(CodeFileIndex))
    assert n == 0                                # nothing persisted without a session
