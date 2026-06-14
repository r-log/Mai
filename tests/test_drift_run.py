from mai.drift.fake import FakeTreeClient
from mai.drift.run import compute_drift, default_pairs
from mai.repository.drift import DriftRepository
from mai.repository.repos import RepoRepository


async def test_compute_drift_stores_per_subsystem(session):
    client = FakeTreeClient({
        "mangoszero/server": {"src/game/Object/Player.cpp": "a", "common.txt": "c"},
        "mangostwo/server": {"src/game/Object/Player.cpp": "b", "common.txt": "c"},
    })
    n = await compute_drift(session, client,
                            [("mangoszero/server", "mangostwo/server")], depth=3)
    await session.commit()
    assert n == 2
    rows = {r.subsystem: r for r in
            await DriftRepository(session).for_pair("mangoszero/server", "mangostwo/server")}
    assert rows["src/game/Object"].diverged == 1
    assert rows["(root)"].identical == 1


async def test_default_pairs_builds_pairs_of_server_repos(session):
    rr = RepoRepository(session)
    await rr.upsert("mangoszero/server", "zero", "u")
    await rr.upsert("mangostwo/server", "two", "u")
    await rr.upsert("mangoszero/database", "zero", "u")  # not a server repo
    await session.commit()
    pairs = await default_pairs(session)
    assert len(pairs) == 1
    assert set(pairs[0]) == {"mangoszero/server", "mangostwo/server"}
