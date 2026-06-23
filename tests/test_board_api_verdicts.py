import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from mai.auth.accounts import create_account
from mai.auth.fake import FakeHasher
from mai.db.base import Base
from mai.db.models import Commit, PatchGroup, PortVerdict, Repo
from mai.web.app import create_app


@pytest_asyncio.fixture
async def env():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    hasher = FakeHasher()
    pws = {}
    async with factory() as s:
        pws["antz"] = await create_account(s, hasher, "antz", is_maintainer=True)
        pws["dev"] = await create_account(s, hasher, "dev", is_maintainer=False)
        # Seed a PatchGroup + Commit + PortVerdicts for the tests that check fixes
        s.add(Repo(full_name="mangosthree/server", core="three",
                   url="https://github.com/mangosthree/server"))
        s.add(PatchGroup(id="pg1", patch_id="pid1"))
        s.add(Commit(core="three", sha="abc1234567", author="a", authored_at="t",
                     committer="a", committed_at="t", message="Fix shared thing",
                     parent_shas=[], is_merge=False))
        # two needs the fix (claimable), one should review, four is n/a
        s.add(PortVerdict(patch_group_id="pg1", core="two", verdict="needs",
                          apply_result="clean", relevance="portable",
                          source_core="three", source_sha="abc1234567",
                          subsystem="src/shared/Database", magnitude=10,
                          tier="surgical"))
        s.add(PortVerdict(patch_group_id="pg1", core="one", verdict="review",
                          apply_result="conflict", relevance="divergent",
                          source_core="three", source_sha="abc1234567",
                          subsystem="src/shared/Database", magnitude=10,
                          tier="surgical", conflict_applied=4, conflict_total=5))
        s.add(PortVerdict(patch_group_id="pg1", core="four", verdict="not_applicable",
                          apply_result="file_absent", relevance="divergent",
                          source_core="three", source_sha="abc1234567",
                          subsystem="src/shared/Database", magnitude=10,
                          tier="surgical"))
        await s.commit()
    app = create_app(factory, hasher, "test-secret", cookie_secure=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test",
                           follow_redirects=False) as ac:
        yield ac, factory, pws


async def _login(ac, username, pw):
    await ac.post("/login", data={"username": username, "password": pw})
    await ac.post("/set-password", data={"new_password": "a-good-long-password"})


async def _csrf(ac):
    return (await ac.get("/api/board")).json()["csrf"]


async def test_board_returns_fixes_not_columns(env):
    """GET /api/board must return 'fixes' key and NOT 'columns'."""
    ac, _, pws = env
    await _login(ac, "dev", pws["dev"])
    r = await ac.get("/api/board")
    assert r.status_code == 200
    body = r.json()
    assert "fixes" in body, "response must have 'fixes'"
    assert "columns" not in body, "response must NOT have 'columns'"


async def test_board_has_required_top_level_keys(env):
    """GET /api/board has summary, cores, fixes, _orphans, csrf, me."""
    ac, _, pws = env
    await _login(ac, "antz", pws["antz"])
    body = (await ac.get("/api/board")).json()
    for key in ("summary", "cores", "fixes", "_orphans", "csrf", "me"):
        assert key in body, f"missing key: {key}"
    assert isinstance(body["csrf"], str) and body["csrf"]
    assert body["me"] == {"username": "antz", "is_maintainer": True}


async def test_summary_needs_is_int(env):
    """summary.needs must be a non-negative integer."""
    ac, _, pws = env
    await _login(ac, "dev", pws["dev"])
    body = (await ac.get("/api/board")).json()
    assert isinstance(body["summary"]["needs"], int)
    assert body["summary"]["needs"] >= 0


async def test_fixes_contain_seeded_card(env):
    """The seeded pg1 verdict must appear as a fix card with a needs entry for core 'two'."""
    ac, _, pws = env
    await _login(ac, "dev", pws["dev"])
    body = (await ac.get("/api/board")).json()
    assert len(body["fixes"]) == 1
    card = body["fixes"][0]
    assert card["id"] == "pg1"
    assert card["title"] == "Fix shared thing"
    needs_cores = [e["core"] for e in card["needs"]]
    assert "two" in needs_cores
    assert "columns" not in body


async def test_claim_adds_board_overlay_to_needs_entry(env):
    """After claiming pg1:two, the needs entry for core 'two' gains entry['board']['assignee']."""
    ac, _, pws = env
    await _login(ac, "dev", pws["dev"])
    token = await _csrf(ac)

    r = await ac.post("/api/board/pg1:two/claim", json={"csrf": token})
    assert r.status_code == 200
    assert r.json()["assignee"] == "dev"

    body = (await ac.get("/api/board")).json()
    card = body["fixes"][0]
    # find the 'two' needs entry
    two_entry = next(e for e in card["needs"] if e["core"] == "two")
    assert "board" in two_entry, "claimed needs entry must have 'board' overlay"
    assert two_entry["board"]["assignee"] == "dev"


async def test_claim_adds_board_overlay_to_review_entry(env):
    """After claiming pg1:one (a review entry), that entry gains entry['board']['assignee']."""
    ac, _, pws = env
    await _login(ac, "dev", pws["dev"])
    token = await _csrf(ac)

    r = await ac.post("/api/board/pg1:one/claim", json={"csrf": token})
    assert r.status_code == 200

    body = (await ac.get("/api/board")).json()
    card = body["fixes"][0]
    one_entry = next(e for e in card["review"] if e["core"] == "one")
    assert "board" in one_entry, "claimed review entry must have 'board' overlay"
    assert one_entry["board"]["assignee"] == "dev"


async def test_orphan_board_item_appears_in_orphans(env):
    """A BoardItem with no matching active needs/review entry appears in _orphans."""
    ac, _, pws = env
    await _login(ac, "dev", pws["dev"])
    token = await _csrf(ac)

    # Claim an id that has no matching PortVerdict row
    r = await ac.post("/api/board/pgX:three/claim", json={"csrf": token})
    assert r.status_code == 200

    body = (await ac.get("/api/board")).json()
    orphan_ids = {o["port_candidate_id"] for o in body["_orphans"]}
    assert "pgX:three" in orphan_ids
